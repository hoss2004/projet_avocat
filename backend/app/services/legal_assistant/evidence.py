from collections import defaultdict
from sqlalchemy import or_, select

from app.repositories.legal import LegalRepository
from app.models.knowledge import SearchChunk
from app.schemas.assistant import Source
from app.services.exact_reference.service import ExactReferenceService
from app.services.legal_assistant.models import (
    EvidenceSourceType,
    EvidenceStatus,
    LegalEvidence,
    LegalEvidencePack,
    LegalIntent,
    LegalIssue,
    LegalToolName,
    LegalUserRequest,
)
from app.services.retrieval.quality import RetrievalQualityGate
from app.services.retrieval.search import retrieve
from app.services.retrieval.understanding import source_identity
from app.utils.arabic import normalize_for_search


class EvidencePackBuilder:
    def build(
        self,
        issues: list[LegalIssue],
        sources: list[Source],
        *,
        explicit_source_ids: set[str] | None = None,
        missing_information: list[str] | None = None,
    ) -> LegalEvidencePack:
        explicit_source_ids = explicit_source_ids or set()
        records = [self._record(source, issues, str(source.id) in explicit_source_ids) for source in sources]
        statutes = [item for item in records if item.source_type in {EvidenceSourceType.STATUTE, EvidenceSourceType.REGULATION, EvidenceSourceType.CONSTITUTION}]
        case_law = [item for item in records if item.source_type == EvidenceSourceType.CASE_LAW]
        case_documents = [item for item in records if item.source_type in {EvidenceSourceType.CONTRACT, EvidenceSourceType.COURT_DOCUMENT, EvidenceSourceType.EXPERT_REPORT, EvidenceSourceType.CLIENT_DOCUMENT, EvidenceSourceType.CORRESPONDENCE, EvidenceSourceType.ADMINISTRATIVE_DOCUMENT}]
        used = {item.evidence_id for item in statutes + case_law + case_documents}
        return LegalEvidencePack(
            legal_issues=issues,
            statutes=statutes,
            case_law=case_law,
            case_documents=case_documents,
            explicit_references=[item for item in records if item.is_explicit_reference],
            other_evidence=[item for item in records if item.evidence_id not in used],
            missing_information=list(dict.fromkeys(missing_information or [])),
        )

    def _record(self, source: Source, issues: list[LegalIssue], explicit: bool) -> LegalEvidence:
        source_id = str(source.id)
        metadata = source.metadata or {}
        hierarchy = {
            key: metadata.get(key)
            for key in (
                "document_type", "court", "chamber", "decision_date", "case_number",
                "authority", "version", "version_status", "status", "source_url",
                "book", "title_section", "chapter", "section", "subsection",
            )
            if metadata.get(key) is not None
        }
        return LegalEvidence(
            evidence_id=f"EVID-{source_id}",
            source_id=source_id,
            source_type=self._source_type(source),
            title=source.title,
            original_text=source.original_text,
            article_number=source.article_number,
            page_start=source.page_start,
            page_end=source.page_end,
            language=source.language,
            document_id=str(source.document_id),
            case_id=str(source.case_id) if source.case_id else None,
            issue_ids=[issue.id for issue in issues if f"EVID-{source_id}" in issue.evidence_ids],
            is_explicit_reference=explicit,
            hierarchy=hierarchy,
            metadata=metadata,
        )

    def _source_type(self, source: Source) -> EvidenceSourceType:
        metadata = source.metadata or {}
        document_type = str(metadata.get("document_type") or metadata.get("category") or "").casefold()
        if source.source_type == "law":
            if "jurisprud" in document_type or document_type in {"decision", "case_law"}:
                return EvidenceSourceType.CASE_LAW
            if "constitution" in document_type:
                return EvidenceSourceType.CONSTITUTION
            if "regulation" in document_type or "décret" in document_type or "decret" in document_type:
                return EvidenceSourceType.REGULATION
            return EvidenceSourceType.STATUTE
        mappings = {
            "contract": EvidenceSourceType.CONTRACT,
            "contrat": EvidenceSourceType.CONTRACT,
            "court": EvidenceSourceType.COURT_DOCUMENT,
            "judgment": EvidenceSourceType.COURT_DOCUMENT,
            "expert": EvidenceSourceType.EXPERT_REPORT,
            "correspond": EvidenceSourceType.CORRESPONDENCE,
            "email": EvidenceSourceType.CORRESPONDENCE,
            "administrative": EvidenceSourceType.ADMINISTRATIVE_DOCUMENT,
        }
        for marker, kind in mappings.items():
            if marker in document_type:
                return kind
        return EvidenceSourceType.CLIENT_DOCUMENT if source.source_type == "case_document" else EvidenceSourceType.OTHER


class EvidenceRetriever:
    """Retrieve each issue independently and merge only source-backed evidence."""

    def retrieve(
        self,
        session,
        tenant_id,
        request,
        settings,
        user_request: LegalUserRequest,
        issues: list[LegalIssue],
        exact_reference_query=None,
        tool_calls=None,
        max_tool_calls=8,
    ):
        repository = LegalRepository(session, tenant_id)
        by_id: dict[str, Source] = {}
        issue_sources: dict[str, list[str]] = defaultdict(list)
        warnings: list[str] = []
        missing: list[str] = []
        explicit_ids: set[str] = set()
        trace = {
            "issue_retrievals": [], "explicit_reference": None,
            "tool_budget": max_tool_calls, "source_relevance_decisions": [],
        }
        allowed_tools = {call.name for call in tool_calls} if tool_calls is not None else None
        calls_used = 0
        issue_query_text=" ".join(issue.search_queries[0] for issue in issues).casefold()
        targeted_contract_lookup=(
            user_request.primary_intent == LegalIntent.LEGAL_OPINION
            and "force obligatoire" in issue_query_text
            and "empêchement du créancier" in issue_query_text
            and "mise en demeure" in issue_query_text
        )
        retrieval_settings=(
            settings.model_copy(update={"embedding_provider":"disabled"})
            if targeted_contract_lookup else settings
        )
        trace["retrieval_mode"]=(
            "targeted_contract_provisions" if targeted_contract_lookup else "hybrid"
        )
        targeted_sources=(
            self._targeted_contract_sources(session, tenant_id)
            if targeted_contract_lookup else []
        )

        if exact_reference_query and user_request.explicit_references:
            calls_used += 1
            exact = ExactReferenceService().execute(repository, exact_reference_query, request)
            trace["explicit_reference"] = exact.get("debug")
            for source in exact["sources"]:
                source_id = str(source.id)
                by_id[source_id] = source
                explicit_ids.add(source_id)
                for issue in issues:
                    if source_id not in issue_sources[issue.id]:
                        issue_sources[issue.id].append(source_id)
            if exact["status"] != "FOUND":
                missing.append(f"Référence explicite non résolue: {exact['status']}")
                warnings.append(exact["status"])

        for issue_index,issue in enumerate(issues):
            searchable = allowed_tools is None or bool(allowed_tools & {
                LegalToolName.SEARCH_LEGAL_SOURCES,
                LegalToolName.SEARCH_CASE_LAW,
                LegalToolName.SEARCH_CASE_DOCUMENTS,
                LegalToolName.SEARCH_CURRENT_CASE,
            })
            if not searchable or calls_used >= max_tool_calls:
                trace["issue_retrievals"].append({
                    "issue_id":issue.id,
                    "query":issue.search_queries[0],
                    "candidate_count":0,
                    "accepted_count":0,
                    "quality_gate":{"status":"not_requested_or_budget_exhausted", "checks":[]},
                })
                continue
            updates = {"question":issue.search_queries[0]}
            if allowed_tools is not None:
                case_search = LegalToolName.SEARCH_CASE_DOCUMENTS in allowed_tools or LegalToolName.SEARCH_CURRENT_CASE in allowed_tools
                legal_search = LegalToolName.SEARCH_LEGAL_SOURCES in allowed_tools or LegalToolName.SEARCH_CASE_LAW in allowed_tools
                if case_search and not legal_search:
                    updates["scope"] = "CASE_ONLY"
                elif legal_search and not case_search:
                    updates["scope"] = "LEGAL_ONLY"
                if LegalToolName.SEARCH_CASE_LAW in allowed_tools and LegalToolName.SEARCH_LEGAL_SOURCES not in allowed_tools:
                    updates["legal_corpus"] = "jurisprudence"
            issue_request = request.model_copy(update=updates)
            issue_trace = {}
            if targeted_contract_lookup:
                candidates=(
                    [targeted_sources[issue_index]]
                    if issue_index < len(targeted_sources) and targeted_sources[issue_index] is not None
                    else []
                )
                issue_warnings=[]
                issue_trace={"strategy":"exact_normalized_legal_phrase"}
            else:
                candidates, issue_warnings = retrieve(
                    session,
                    tenant_id,
                    issue_request,
                    retrieval_settings,
                    issue_trace if request.debug else None,
                )
            calls_used += 1
            if user_request.primary_intent == LegalIntent.ARGUMENTATION and calls_used < max_tool_calls:
                opposition = issue_request.model_copy(update={"question":issue.description + " exceptions nullité compétence délais الاستثناءات البطلان الاختصاص"})
                opposite, extra_warnings = retrieve(session, tenant_id, opposition, retrieval_settings)
                calls_used += 1
                existing = {str(source.id) for source in candidates}
                candidates += [source for source in opposite if str(source.id) not in existing]
                candidates = candidates[:request.top_k]
                issue_warnings += extra_warnings
            accepted, gate = RetrievalQualityGate().evaluate(issue.search_queries[0], candidates, settings)
            relevance = {
                check["source_id"]:check.get("source_relevance_decision", "REJECT")
                for check in gate.get("checks", [])
            }
            accepted = [source.model_copy(update={
                "metadata":{
                    **source.metadata,
                    "source_relevance_decision":relevance.get(str(source.id), "USE"),
                },
            }) for source in accepted]
            trace["source_relevance_decisions"].extend({
                **check, "issue_id":issue.id,
            } for check in gate.get("checks", []))
            attempts = [{"query": issue_request.question, "candidates":len(candidates), "accepted":len(accepted)}]
            if (
                not accepted
                and calls_used < max_tool_calls
                and len(issues) > 1
                and user_request.primary_intent in {
                    LegalIntent.LEGAL_OPINION, LegalIntent.CASE_ANALYSIS,
                    LegalIntent.ARGUMENTATION, LegalIntent.PROCEDURAL_ANALYSIS,
                }
            ):
                followup_query = f"{issue.description} {user_request.original_question}"[:4000]
                followup_request = issue_request.model_copy(update={"question":followup_query})
                followup, followup_warnings = retrieve(
                    session, tenant_id, followup_request, retrieval_settings,
                )
                calls_used += 1
                followup_accepted, followup_gate = RetrievalQualityGate().evaluate(
                    issue.search_queries[0], followup, settings,
                )
                attempts.append({
                    "query":followup_query, "candidates":len(followup),
                    "accepted":len(followup_accepted), "quality_gate":followup_gate,
                })
                existing = {str(source.id) for source in accepted}
                accepted += [source for source in followup_accepted if str(source.id) not in existing]
                candidates += [source for source in followup if str(source.id) not in {str(item.id) for item in candidates}]
                issue_warnings += followup_warnings
            warnings.extend(issue_warnings)
            for source in accepted:
                source_id = str(source.id)
                by_id.setdefault(source_id, source)
                if source_id not in issue_sources[issue.id]:
                    issue_sources[issue.id].append(source_id)
            trace["issue_retrievals"].append({
                "issue_id": issue.id,
                "query": issue.search_queries[0],
                "candidate_count": len(candidates),
                "accepted_count": len(accepted),
                "quality_gate": gate,
                "attempts": attempts,
                **({"retrieval": issue_trace} if request.debug else {}),
            })

        unresolved_explicit = bool(user_request.explicit_references and not explicit_ids)
        updated_issues = []
        for issue in issues:
            ids = issue_sources[issue.id]
            if not ids:
                status = EvidenceStatus.NO_RELIABLE_SOURCE
                missing.append(f"{issue.id}: aucune source suffisamment fiable")
            elif unresolved_explicit:
                status = EvidenceStatus.PARTIAL
            else:
                status = EvidenceStatus.SUPPORTED
            updated_issues.append(issue.model_copy(update={
                "evidence_ids": [f"EVID-{source_id}" for source_id in ids],
                "evidence_status": status,
            }))

        # Rebuild the source-to-issue links using the final issue objects.
        sources = list(by_id.values())
        pack = EvidencePackBuilder().build(
            updated_issues,
            sources,
            explicit_source_ids=explicit_ids,
            missing_information=missing,
        )
        trace["tool_calls_used"] = calls_used
        trace["tool_budget_remaining"] = max(0, max_tool_calls - calls_used)
        return pack, sources, list(dict.fromkeys(warnings)), trace

    def _targeted_contract_sources(self, session, tenant_id):
        """Resolve stable COC concepts by their original Arabic wording."""
        marker_groups=(
            ("ما انعقد على الوجه الصحيح", "يقوم مقام القانون"),
            ("عدم الوفاء بالعقد", "تأخر المدين عن الوفاء"),
            ("فعل الدائن", "سبب آخر ينسب إليه"),
        )
        base=select(SearchChunk).where(
            SearchChunk.tenant_id == tenant_id,
            SearchChunk.source_type == "law",
            or_(
                SearchChunk.metadata_json["code_id"].as_string() == "coc",
                SearchChunk.metadata_json["legal_domain"].as_string() == "contract_law",
            ),
        )
        sources=[]
        for markers in marker_groups:
            conditions=[
                SearchChunk.search_text.contains(normalize_for_search(marker), autoescape=True)
                for marker in markers
            ]
            chunk=session.scalar(base.where(or_(*conditions)).order_by(SearchChunk.id).limit(1))
            sources.append(self._source_from_chunk(chunk) if chunk is not None else None)
        return sources

    def _source_from_chunk(self, chunk):
        domain,code=source_identity(chunk.title,chunk.metadata_json)
        return Source(
            id=chunk.id, source_type=chunk.source_type, document_id=chunk.document_id,
            article_id=chunk.article_id, case_id=chunk.case_id, title=chunk.title,
            article_number=chunk.article_number, original_text=chunk.original_text,
            language=chunk.language, page_start=chunk.page_start, page_end=chunk.page_end,
            metadata={**chunk.metadata_json, "domain":domain, "code_id":code},
            score=10.0,
            file_url=f"/legal-documents/{chunk.document_id}/file",
        )
