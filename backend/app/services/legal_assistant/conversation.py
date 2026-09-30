from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
import re
import unicodedata
from uuid import UUID

from sqlalchemy import func, select

from app.models.knowledge import (
    Case,
    CaseDocument,
    CaseState,
    ConversationState,
    ConversationTurn,
    LegalDraft,
    LegalDraftVersion,
)


class ConversationScopeError(ValueError):
    pass


_TARGET_STOPWORDS = {
    "la", "le", "les", "un", "une", "des", "du", "de", "d", "sur", "dans",
    "partie", "section", "passage", "paragraphe", "argument", "concernant",
    "relative", "relatif", "uniquement", "cette", "ce", "cet", "au", "aux",
}


def _search_form(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def resolve_draft_section(sections: list[dict], target: str | None, instruction: str = "") -> int | None:
    """Resolve natural references against titles and content without exposing internal IDs."""
    if not sections:
        return None
    target = (target or "").strip()
    combined = _search_form(" ".join(value for value in (target, instruction) if value))
    if target == "__LAST__" or any(marker in combined for marker in ("dernier paragraphe", "derniere partie")):
        return len(sections) - 1

    normalized_target = _search_form(target)
    if normalized_target:
        for index, item in enumerate(sections):
            if _search_form(item.get("title", "")) == normalized_target:
                return index

    semantic_query = normalized_target or combined
    query_tokens = {
        token for token in semantic_query.split()
        if len(token) > 2 and token not in _TARGET_STOPWORDS
    }
    scored: list[tuple[float, int]] = []
    for index, item in enumerate(sections):
        title = _search_form(item.get("title", ""))
        content = _search_form(item.get("content", "")[:2500])
        title_tokens = set(title.split())
        content_tokens = set(content.split())
        title_overlap = len(query_tokens & title_tokens) / max(1, len(query_tokens))
        content_overlap = len(query_tokens & content_tokens) / max(1, len(query_tokens))
        similarity = SequenceMatcher(None, normalized_target or combined, title).ratio() if title else 0
        phrase = 1.0 if normalized_target and (
            normalized_target in title or normalized_target in content
        ) else 0.0
        score = phrase * 0.20 + title_overlap * 0.35 + content_overlap * 0.40 + similarity * 0.05
        scored.append((score, index))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < 0.24:
        return None
    if len(scored) > 1 and scored[0][0] < 0.70 and scored[0][0] - scored[1][0] < 0.07:
        return None
    return scored[0][1]


@dataclass
class ConversationContext:
    conversation: ConversationState
    case_state: CaseState | None
    active_draft: LegalDraft | None
    prompt_context: dict


class PromptContextBuilder:
    """Select useful state instead of injecting the complete raw history."""

    def build(self, conversation, case_state, draft, turns):
        recent = [
            {"role": item.role, "content": item.content[:2000]}
            for item in turns[-8:]
        ]
        case = None
        if case_state:
            case = {
                "case_id": str(case_state.case_id),
                "parties": case_state.parties,
                "roles": case_state.roles,
                "facts": case_state.facts[-30:],
                "allegations": case_state.allegations[-20:],
                "disputed_facts": case_state.disputed_facts[-20:],
                "timeline": case_state.timeline[-30:],
                "legal_issues": case_state.legal_issues[-20:],
                "documents": case_state.documents[-30:],
                "verified_sources": case_state.verified_sources[-30:],
                "open_questions": case_state.open_questions[-20:],
                "notes": case_state.notes[-20:],
            }
        active_draft = None
        if draft:
            active_draft = {
                "draft_id": str(draft.id),
                "document_type": draft.document_type,
                "title": draft.title,
                "version": draft.version,
                "sections": [
                    {"title":item.get("title", ""), "summary":item.get("content", "")[:240]}
                    for item in draft.sections
                ],
                "sources_used": draft.sources_used,
            }
        return {
            "conversation_id": str(conversation.id),
            "recent_turns": recent,
            "current_focus": conversation.current_focus,
            "last_modified_section": conversation.last_modified_section,
            "conversation_focus": {
                "active_case_id": str(conversation.current_case_id) if conversation.current_case_id else None,
                "active_draft_id": str(conversation.current_draft_id) if conversation.current_draft_id else None,
                "last_assistant_response_id": str(conversation.last_assistant_response_id) if conversation.last_assistant_response_id else None,
                "last_modified_section": conversation.last_modified_section,
                "last_discussed_issue": conversation.last_discussed_issue,
            },
            "pending_questions": conversation.pending_questions,
            "case_state": case,
            "active_draft": active_draft,
        }


class ConversationMemory:
    def prepare(self, session, tenant_id, request) -> ConversationContext:
        conversation = None
        if request.conversation_id:
            conversation = session.scalar(select(ConversationState).where(
                ConversationState.id == request.conversation_id,
                ConversationState.tenant_id == tenant_id,
            ))
            if conversation is None:
                raise ConversationScopeError("Conversation inaccessible")
        if conversation is None:
            conversation = ConversationState(
                tenant_id=tenant_id,
                current_case_id=request.case_id,
                recent_turns=[],
                pending_questions=[],
            )
            session.add(conversation)
            session.flush()

        if request.case_id is not None:
            accessible = session.scalar(select(Case.id).where(
                Case.id == request.case_id,
                Case.tenant_id == tenant_id,
            ))
            if accessible is None:
                raise ConversationScopeError("Dossier inaccessible")
            if conversation.current_case_id != request.case_id:
                conversation.current_case_id = request.case_id
                if conversation.current_draft_id:
                    current = session.scalar(select(LegalDraft).where(
                        LegalDraft.id == conversation.current_draft_id,
                        LegalDraft.tenant_id == tenant_id,
                    ))
                    if current and current.case_id != request.case_id:
                        conversation.current_draft_id = None

        case_state = self._case_state(session, tenant_id, conversation.current_case_id)
        active_draft = None
        if conversation.current_draft_id:
            active_draft = session.scalar(select(LegalDraft).where(
                LegalDraft.id == conversation.current_draft_id,
                LegalDraft.tenant_id == tenant_id,
            ))
        turns = session.scalars(select(ConversationTurn).where(
            ConversationTurn.tenant_id == tenant_id,
            ConversationTurn.conversation_id == conversation.id,
        ).order_by(ConversationTurn.ordinal.desc()).limit(8)).all()
        turns = list(reversed(turns))
        return ConversationContext(
            conversation=conversation,
            case_state=case_state,
            active_draft=active_draft,
            prompt_context=PromptContextBuilder().build(conversation, case_state, active_draft, turns),
        )

    def _case_state(self, session, tenant_id, case_id):
        if not case_id:
            return None
        state = session.scalar(select(CaseState).where(
            CaseState.tenant_id == tenant_id,
            CaseState.case_id == case_id,
        ))
        case = session.scalar(select(Case).where(Case.tenant_id == tenant_id, Case.id == case_id))
        if state is None:
            parties = [name for name in [case.client_name, case.opponent_name] if name]
            roles = {}
            if case.client_name:
                roles[case.client_name] = "client"
            if case.opponent_name:
                roles[case.opponent_name] = "partie_adverse"
            state = CaseState(
                tenant_id=tenant_id,
                case_id=case_id,
                parties=parties,
                roles=roles,
                facts=[], allegations=[], disputed_facts=[], timeline=[], legal_issues=[],
                documents=[], verified_sources=[], open_questions=[], notes=[],
            )
            session.add(state)
            session.flush()
        documents = session.scalars(select(CaseDocument).where(
            CaseDocument.tenant_id == tenant_id,
            CaseDocument.case_id == case_id,
        ).order_by(CaseDocument.created_at)).all()
        state.documents = [
            {"id": str(item.id), "filename": item.filename, "document_type": item.document_type}
            for item in documents
        ]
        return state

    def add_turn(self, session, tenant_id, conversation, role, content, metadata=None):
        ordinal = session.scalar(select(func.max(ConversationTurn.ordinal)).where(
            ConversationTurn.tenant_id == tenant_id,
            ConversationTurn.conversation_id == conversation.id,
        )) or 0
        turn = ConversationTurn(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            ordinal=ordinal + 1,
            role=role,
            content=content,
            metadata_json=metadata or {},
        )
        session.add(turn)
        session.flush()
        recent = list(conversation.recent_turns or [])
        recent.append({"role": role, "content": content[:2000], "ordinal": ordinal + 1})
        conversation.recent_turns = recent[-8:]
        conversation.updated_at = datetime.now(timezone.utc)
        if role == "assistant":
            conversation.last_assistant_response_id = turn.id
        return turn

    def add_case_facts(self, case_state, facts, source="user"):
        if not case_state:
            return []
        existing = {item.get("text", "").strip().casefold() for item in case_state.facts}
        added = []
        values = list(case_state.facts)
        for text in facts:
            text = text.strip()
            if not text or text.casefold() in existing:
                continue
            item = {
                "id": f"FACT-{len(values) + 1}",
                "text": text,
                "status": "user_provided",
                "source": source,
            }
            values.append(item)
            added.append(item)
            existing.add(text.casefold())
        case_state.facts = values
        case_state.updated_at = datetime.now(timezone.utc)
        return added


class DraftManager:
    def create(self, session, tenant_id, conversation, case_id, document_type, title, content, sections, source_ids):
        draft = LegalDraft(
            tenant_id=tenant_id,
            case_id=case_id,
            conversation_id=conversation.id,
            document_type=document_type,
            title=title,
            content=content,
            sections=sections,
            version=1,
            sources_used=source_ids,
        )
        session.add(draft)
        session.flush()
        session.add(LegalDraftVersion(
            tenant_id=tenant_id,
            draft_id=draft.id,
            version=1,
            content=content,
            sections=sections,
            sources_used=source_ids,
            operation="CREATE",
        ))
        conversation.current_draft_id = draft.id
        conversation.last_modified_section = sections[-1]["title"] if sections else None
        conversation.updated_at = datetime.now(timezone.utc)
        return draft

    def revise(self, session, tenant_id, conversation, draft, revision, operation, instruction, source_ids=None):
        version = draft.version + 1
        draft.content = revision.content
        draft.sections = [item.model_dump() for item in revision.sections]
        draft.version = version
        if source_ids:
            draft.sources_used = list(dict.fromkeys([*draft.sources_used, *source_ids]))
        draft.updated_at = datetime.now(timezone.utc)
        session.add(LegalDraftVersion(
            tenant_id=tenant_id,
            draft_id=draft.id,
            version=version,
            content=draft.content,
            sections=draft.sections,
            sources_used=draft.sources_used,
            operation=operation,
            instruction=instruction,
        ))
        conversation.current_draft_id = draft.id
        conversation.last_modified_section = revision.modified_section
        conversation.current_focus = revision.modified_section or conversation.current_focus
        conversation.updated_at = datetime.now(timezone.utc)
        return draft

    def apply_patch(self, session, tenant_id, conversation, draft, patch, operation, instruction, source_ids=None):
        from app.services.legal_assistant.models import DraftRevisionOutput

        if patch.full_rewrite is not None:
            updated = self.revise(
                session, tenant_id, conversation, draft, patch.full_rewrite,
                operation, instruction, source_ids,
            )
            return updated, patch.full_rewrite.modified_section, patch.full_rewrite.content

        sections = [dict(item) for item in draft.sections]
        target = patch.target_section.strip()
        index = resolve_draft_section(sections, target, instruction)
        if index is None:
            raise ConversationScopeError(f"Section cible introuvable : {target}")

        old_title = sections[index].get("title", target)
        if patch.delete_target:
            removed = sections.pop(index)
            displayed = f"Section supprimée : {removed.get('title', target)}."
            modified = removed.get("title", target)
        else:
            if not patch.replacement_content:
                raise ConversationScopeError("La modification ne contient aucun texte de remplacement.")
            sections[index] = {
                "title":patch.replacement_title or old_title,
                "content":patch.replacement_content,
            }
            modified = sections[index]["title"]
            displayed = modified + "\n" + sections[index]["content"]
        if not sections:
            raise ConversationScopeError("Le brouillon ne peut pas être entièrement vidé par une modification ciblée.")
        content = "\n\n".join(item["title"] + "\n" + item["content"] for item in sections)
        revision = DraftRevisionOutput(
            content=content,
            sections=sections,
            modified_section=modified,
            explanation=patch.explanation,
        )
        updated = self.revise(
            session, tenant_id, conversation, draft, revision,
            operation, instruction, source_ids,
        )
        return updated, modified, displayed
