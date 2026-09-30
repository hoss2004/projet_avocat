import hashlib

from pydantic import ValidationError

from app.models.knowledge import AuditEvent
from app.schemas.assistant import Source
from app.services.citations.validator import CitationError
from app.services.embeddings.providers import ProviderError
from app.services.legal_assistant.evidence import EvidenceRetriever
from app.services.legal_assistant.intent import LegalIntentDecision, LegalIntentRouter
from app.services.legal_assistant.issues import LegalIssueAnalyzer
from app.services.legal_assistant.models import (
    CaseFact,
    ConversationReply,
    DraftEditOutput,
    LegalIntent,
    TimelineEvent,
)
from app.services.legal_assistant.agent import LegalConversationAgent
from app.services.legal_assistant.conversation import (
    ConversationMemory,
    ConversationScopeError,
    DraftManager,
    resolve_draft_section,
)
from app.services.legal_assistant.presentation import (
    humanize_draft_failure,
    humanize_exact_lookup,
    sanitize_user_messages,
)
from app.services.legal_assistant.reasoning import LegalReasoningService
from app.services.llm.providers import llm_provider
from app.services.exact_reference.service import ExactReferenceService


MISSING = {
    "fr": "Les sources disponibles ne permettent pas de confirmer ce point.",
    "ar": "لا تسمح المصادر المتاحة بتأكيد هذه النقطة.",
    "en": "The available sources do not establish this point.",
}


def _finalize_agent_response(
    result,
    decision,
    *,
    case_updates=None,
    draft_updates=None,
    sources=None,
    follow_up_needed=False,
):
    tool_calls = [call.model_dump(mode="json") for call in decision.user_task.tool_calls]
    source_ids = []
    for source in sources or []:
        source_id = getattr(source, "id", None)
        if source_id is None and isinstance(source, dict):
            source_id = source.get("id") or source.get("source_id")
        if source_id is not None:
            source_ids.append(str(source_id))
    return LegalConversationAgent.response_contract(
        result,
        case_updates=case_updates,
        draft_updates=draft_updates,
        sources_used=list(dict.fromkeys(source_ids)),
        tool_calls=tool_calls,
        follow_up_needed=follow_up_needed,
    )


def query_exact_chat(session, tenant_id, request, settings, seed):
    """Run an exact-only turn through conversational state without invoking retrieval or an LLM."""
    from app.repositories.legal import LegalRepository

    memory = ConversationMemory()
    context = memory.prepare(session, tenant_id, request)
    context.prompt_context["turn_before"] = {
        "case_id":str(context.conversation.current_case_id) if context.conversation.current_case_id else None,
        "draft_id":str(context.conversation.current_draft_id) if context.conversation.current_draft_id else None,
    }
    memory.add_turn(session, tenant_id, context.conversation, "user", request.question, {"mode":request.mode})
    decision = LegalConversationAgent(None, settings.max_tool_calls).decide(
        request, LegalRepository(session, tenant_id), seed, context.prompt_context,
    )
    exact = ExactReferenceService().execute(
        LegalRepository(session, tenant_id), decision.exact_reference_query, request
    )
    return _exact_conversation_response(session, tenant_id, request, context, memory, decision, exact)


def query_rag(
    session,
    tenant_id,
    request,
    settings,
    *,
    legal_request=None,
    exact_reference_query=None,
):
    from app.services.case_jobs import enqueue, full_analysis_intent, latest
    from app.repositories.legal import LegalRepository

    memory = ConversationMemory()
    conversation_context = memory.prepare(session, tenant_id, request)
    turn_before = {
        "case_id":str(conversation_context.conversation.current_case_id) if conversation_context.conversation.current_case_id else None,
        "draft_id":str(conversation_context.conversation.current_draft_id) if conversation_context.conversation.current_draft_id else None,
    }
    conversation_context.prompt_context["turn_before"] = turn_before
    stored_questions = [
        item["content"] for item in conversation_context.prompt_context["recent_turns"]
        if item["role"] == "user"
    ][-3:]
    contextual_request = request.model_copy(update={
        "previous_questions": stored_questions or request.previous_questions,
        "case_id": conversation_context.conversation.current_case_id or request.case_id,
    })
    memory.add_turn(
        session, tenant_id, conversation_context.conversation, "user", request.question,
        {"mode": request.mode, "case_id": str(contextual_request.case_id) if contextual_request.case_id else None},
    )

    if legal_request is None:
        seed = LegalIntentRouter().route(contextual_request, LegalRepository(session, tenant_id))
    else:
        seed = LegalIntentDecision(legal_request, exact_reference_query)
    provider = llm_provider(settings)
    agent = LegalConversationAgent(provider, settings.max_tool_calls)
    chat_decision = agent.decide(
        contextual_request, LegalRepository(session, tenant_id), seed,
        conversation_context.prompt_context,
    )
    legal_request = chat_decision.legal_request
    exact_reference_query = chat_decision.exact_reference_query
    user_task = chat_decision.user_task

    if user_task.create_new_case and contextual_request.case_id is None:
        from uuid import uuid4
        from app.models.knowledge import Case

        new_case = Case(
            tenant_id=tenant_id,
            title=user_task.case_title or "Nouveau dossier conversationnel",
            reference="DOS-" + uuid4().hex[:8].upper(),
        )
        session.add(new_case)
        session.flush()
        conversation_context.conversation.current_case_id = new_case.id
        contextual_request = contextual_request.model_copy(update={
            "case_id":new_case.id, "scope":"LEGAL_AND_CASE",
            "conversation_id":conversation_context.conversation.id,
        })
        conversation_context = memory.prepare(session, tenant_id, contextual_request)
        conversation_context.prompt_context["turn_before"] = turn_before
        memory.add_case_facts(conversation_context.case_state, [request.question])
        legal_request = legal_request.model_copy(update={"case_id":new_case.id})
        user_task = user_task.model_copy(update={"case_id":new_case.id})

    if legal_request.primary_intent == LegalIntent.EXACT_REFERENCE_QUERY:
        return _exact_conversation_response(
            session, tenant_id, contextual_request, conversation_context, memory,
            chat_decision, ExactReferenceService().execute(
                LegalRepository(session, tenant_id), exact_reference_query, contextual_request
            ),
        )

    if legal_request.primary_intent in {LegalIntent.SIMPLE_CHAT, LegalIntent.NEW_CASE_FACT}:
        return _conversation_only_response(
            session, tenant_id, contextual_request, provider, conversation_context,
            memory, chat_decision,
        )

    if (
        contextual_request.case_id
        and legal_request.primary_intent == LegalIntent.CASE_ANALYSIS
        and full_analysis_intent(contextual_request.question)
    ):
        from sqlalchemy import select
        from app.models.knowledge import Case
        from app.services.retrieval.search import ScopeError

        if not session.scalar(select(Case.id).where(Case.id == contextual_request.case_id, Case.tenant_id == tenant_id)):
            raise ScopeError("Dossier inaccessible")
        job = enqueue(session, tenant_id, contextual_request.case_id)
        memory.add_turn(
            session, tenant_id, conversation_context.conversation, "assistant",
            "L'analyse complète du dossier est lancée.",
            {"primary_goal":"CASE_ANALYSIS", "analysis_id":str(job.id)},
        )
        session.commit()
        queued_result = {
            "answer": "L'analyse complète du dossier est lancée. Son avancement et le rapport sont disponibles dans Dossiers.",
            "mode": "case_analysis_queued",
            "answer_kind": "ANALYSE_IA",
            "primary_intent": legal_request.primary_intent.value,
            "primary_goal": user_task.primary_goal.value,
            "user_task": user_task.model_dump(mode="json"),
            "legal_user_request": legal_request.model_dump(mode="json"),
            "analysis_id": str(job.id),
            "conversation_id": str(conversation_context.conversation.id),
            "draft_created": False,
            "draft_id": str(conversation_context.active_draft.id) if conversation_context.active_draft else None,
            "claims": [],
            "claim_validation": [],
            "citations": [],
            "retrieved_sources": [],
            "legal_issues": [],
            "evidence_pack": None,
            "missing_information": [],
            "warnings": [],
            "confidence": {},
        }
        return _finalize_agent_response(queued_result, chat_decision)

    retrieval_request = _with_conversation_context(session, tenant_id, contextual_request, latest)
    analysis_request = legal_request.model_copy(update={"original_question": retrieval_request.question})
    issues = LegalIssueAnalyzer().analyze(analysis_request, user_task.issue_queries)
    pack, sources, warnings, trace = EvidenceRetriever().retrieve(
        session,
        tenant_id,
        retrieval_request,
        settings,
        legal_request,
        issues,
        exact_reference_query,
        user_task.tool_calls,
        settings.max_tool_calls,
    )
    pack = _enrich_pack_from_case_state(pack, conversation_context.case_state)
    if not sources:
        warnings.append("NO_RELIABLE_SOURCE: Aucune source ne passe le contrôle avant génération.")

    context = _bounded_context(sources, pack)
    if legal_request.primary_intent == LegalIntent.DRAFT_EDIT and conversation_context.active_draft:
        return _revise_draft_response_v2(
            session, tenant_id, contextual_request, provider, conversation_context,
            memory, chat_decision, pack, context, warnings, trace,
        )
    claims = []
    generated_sections = []
    rendered_sections = []
    mode = "extracts_only"
    verification = {"method": "not_generated", "verdicts": []}
    reasoning_llm_called = False
    if provider:
        try:
            reasoning_llm_called = True
            generated, verification = LegalReasoningService().generate(
                contextual_request,
                legal_request,
                pack,
                context,
                provider,
            )
            raw_claims = [claim.model_dump() for claim in generated.claims]
            generated_sections = [claim["section"] for claim in raw_claims]
            claims = _deduplicate_claims(raw_claims)
            rendered_sections = [claim["section"] for claim in claims]
            mode = "generated"
            rejected = verification.get("claims", 0) - verification.get("accepted_claims", 0)
            if rejected:
                warnings.append(f"{rejected} affirmation(s) générée(s) ont été écartées faute de soutien complet.")
            if verification.get("fallback_used"):
                warnings.append("Les règles précises proposées n'ont pas passé la validation; la réponse conserve uniquement l'analyse générale et les vérifications à effectuer.")
        except (ProviderError, ValidationError, CitationError) as exc:
            if request.debug:
                trace["generation_error"] = (
                    {"type": "schema_validation", "errors": exc.errors(include_input=False)}
                    if isinstance(exc, ValidationError)
                    else {"type": exc.__class__.__name__, "message": str(exc)}
                )
            warnings.append(
                str(exc)
                if isinstance(exc, (ProviderError, CitationError))
                else "Réponse du modèle non conforme; extraits originaux affichés."
            )
    else:
        warnings.append("La rédaction conversationnelle est désactivée dans la configuration du modèle.")

    if mode == "generated":
        answer = "\n\n".join(claim["section"] + "\n" + claim["text"] for claim in claims)
        warnings.append("Projet à relire par l'avocat; les sources et les incertitudes restent à vérifier.")
    else:
        answer = {
            "fr":"La rédaction conversationnelle n'a pas pu être validée. Les questions juridiques et les points à vérifier restent visibles; aucune conclusion non vérifiée n'est présentée comme certaine.",
            "ar":"تعذر التحقق من الصياغة الحوارية. تبقى المسائل القانونية والنقاط الواجب التحقق منها ظاهرة، ولم تُعرض أي نتيجة غير موثقة على أنها مؤكدة.",
            "en":"The conversational draft could not be validated. The legal issues and verification points remain visible; no unverified conclusion is presented as certain.",
        }[request.language]

    if any(source.metadata.get("review_required") for source in sources):
        warnings.append("Les documents ou extractions utilisés nécessitent une vérification humaine.")
    if any(source.source_type == "law" and source.metadata.get("status") == "unknown" for source in sources):
        warnings.append("La validité temporelle de certains textes n'est pas renseignée.")

    citation_ids = {quote["source_id"] for claim in claims for quote in claim["citations"]}
    draft = None
    if mode == "generated" and legal_request.primary_intent in {
        LegalIntent.LEGAL_OPINION, LegalIntent.LEGAL_DRAFTING
    }:
        sections = [{"title": claim["section"], "content": claim["text"]} for claim in claims]
        document_type = user_task.requested_document_type or legal_request.requested_output
        title = "Avis juridique" if legal_request.primary_intent == LegalIntent.LEGAL_OPINION else "Brouillon juridique"
        draft = DraftManager().create(
            session, tenant_id, conversation_context.conversation,
            contextual_request.case_id, document_type, title, answer, sections, sorted(citation_ids),
        )
    if conversation_context.case_state:
        conversation_context.case_state.legal_issues = [
            {"id": issue.id, "title": issue.title, "status": issue.evidence_status.value}
            for issue in pack.legal_issues
        ]
        conversation_context.case_state.verified_sources = [
            {"source_id": str(source.id), "title": source.title, "article_number": source.article_number}
            for source in sources if str(source.id) in citation_ids
        ]
        conversation_context.case_state.open_questions = pack.missing_information
    response_draft = draft or conversation_context.active_draft
    memory.add_turn(
        session, tenant_id, conversation_context.conversation, "assistant", answer,
        {"primary_goal": user_task.primary_goal.value, "draft_id": str(response_draft.id) if response_draft else None},
    )
    session.add(AuditEvent(
        tenant_id=tenant_id,
        action="rag_query",
        details={
            "question_sha256": hashlib.sha256(request.question.encode()).hexdigest(),
            "scope": request.scope,
            "case_id": str(request.case_id) if request.case_id else None,
            "sources": [str(source.id) for source in sources],
            "mode": mode,
            "primary_intent": legal_request.primary_intent.value,
            "primary_goal": user_task.primary_goal.value,
            "secondary_goals": [goal.value for goal in user_task.secondary_goals],
            "tool_calls": [call.model_dump(mode="json") for call in user_task.tool_calls],
            "issues": [issue.id for issue in pack.legal_issues],
        },
    ))
    session.commit()

    result = {
        "answer": answer,
        "mode": mode,
        "response_mode": chat_decision.conversation_decision.response_mode.value,
        "intent": chat_decision.conversation_decision.intent.value,
        "conversation_decision": chat_decision.conversation_decision.model_dump(mode="json"),
        "answer_kind": _answer_kind(legal_request.primary_intent),
        "primary_intent": legal_request.primary_intent.value,
        "primary_goal": user_task.primary_goal.value,
        "secondary_goals": [goal.value for goal in user_task.secondary_goals],
        "conversation_id": str(conversation_context.conversation.id),
        "draft_created": draft is not None,
        "draft_id": str(response_draft.id) if response_draft else None,
        "draft_version": response_draft.version if response_draft else None,
        "user_task": user_task.model_dump(mode="json"),
        "legal_user_request": legal_request.model_dump(mode="json"),
        "legal_issues": [
            issue.model_dump(mode="json") for issue in pack.legal_issues
            if issue.title.strip(" -")
        ],
        "evidence_pack": pack.model_dump(mode="json"),
        "reasoning": {
            "status": "GROUNDED_GENERATION" if mode == "generated" else "DOCUMENTARY_FALLBACK",
            "issues": [
                {"id": issue.id, "title": issue.title, "evidence_status": issue.evidence_status.value}
                for issue in pack.legal_issues
            ],
        },
        "claims": claims,
        "claim_validation": verification.get("verdicts", []),
        "citations": [source for source in context if str(source.id) in citation_ids],
        "retrieved_sources": context,
        "missing_information": sanitize_user_messages(
            pack.missing_information or ([MISSING[request.language]] if not context else []),
            request.language,
        ),
        "warnings": sanitize_user_messages(warnings, request.language),
        "confidence": {
            "retrieval_quality": {
                "candidate_count": len(sources),
                "assessment": "per_issue_quality_gate",
            },
            "source_coverage": {
                "law": sum(source.source_type == "law" for source in sources),
                "case": sum(source.source_type == "case_document" for source in sources),
            },
            "citation_coverage": {
                "verified_claims": verification.get("source_supported_claims", len(claims)),
                "total_claims": verification.get("claims", len(claims)),
                "semantic_entailment": verification,
            },
        },
    }
    if request.debug:
        selected = len(sources)
        rejected = sum(
            item.get("candidate_count", 0) - item.get("accepted_count", 0)
            for item in trace.get("issue_retrievals", [])
        )
        result["debug"] = {
            **trace,
            "primary_intent": legal_request.primary_intent.value,
            "primary_goal": user_task.primary_goal.value,
            "secondary_goals": [goal.value for goal in user_task.secondary_goals],
            "current_message": request.question,
            "active_case_id_before": turn_before["case_id"],
            "active_draft_id_before": turn_before["draft_id"],
            "intent": chat_decision.conversation_decision.intent.value,
            "case_action": chat_decision.conversation_decision.case_action.value,
            "draft_action": chat_decision.conversation_decision.draft_action.value,
            "retrieval_required": chat_decision.conversation_decision.retrieval_required,
            "legal_issue_analyzer_called": True,
            "llm_orchestrator_called": chat_decision.llm_orchestrator_called,
            "orchestrator_error": chat_decision.planning_error,
            "tool_calls": [call.model_dump(mode="json") for call in user_task.tool_calls],
            "sources_selected_count": selected,
            "sources_rejected_count": max(0, rejected),
            "reasoning_llm_called": reasoning_llm_called,
            "active_case_id_after": str(conversation_context.conversation.current_case_id) if conversation_context.conversation.current_case_id else None,
            "active_draft_id_after": str(response_draft.id) if response_draft else None,
            "draft_modified": False,
            "draft_loaded_for_generation": False,
            "response_mode": chat_decision.conversation_decision.response_mode.value,
            "generated_sections": generated_sections,
            "rendered_sections": rendered_sections,
            "draft_created": draft is not None,
            "draft_id": str(response_draft.id) if response_draft else None,
            "draft_version": response_draft.version if response_draft else None,
            "claim_validation_called": verification.get("method") != "not_generated",
            "final_response_type": user_task.primary_goal.value,
            "explicit_references": [item.model_dump(mode="json") for item in legal_request.explicit_references],
        }
    return _finalize_agent_response(
        result,
        chat_decision,
        draft_updates=([{
            "draft_id": str(response_draft.id),
            "version": response_draft.version,
            "action": "created",
        }] if draft is not None else []),
        sources=[source for source in context if str(source.id) in citation_ids],
    )


def _exact_conversation_response(session, tenant_id, request, context, memory, decision, exact):
    found = exact["status"] == "FOUND"
    answer = humanize_exact_lookup(exact["status"], request.language)
    memory.add_turn(
        session, tenant_id, context.conversation, "assistant", answer,
        {"primary_goal":"EXACT_REFERENCE_QUERY"},
    )
    session.commit()
    turn = decision.conversation_decision
    result = {
        "query_type": exact["query_type"], "status": exact["status"], "answer": answer,
        "mode":"exact_reference", "response_mode":"EXACT_REFERENCE",
        "intent":turn.intent.value, "conversation_decision":turn.model_dump(mode="json"),
        "answer_kind":"SOURCE_ORIGINALE",
        "primary_intent":"EXACT_REFERENCE_QUERY", "primary_goal":"EXACT_REFERENCE_QUERY",
        "secondary_goals":[], "conversation_id":str(context.conversation.id),
        "draft_created":False, "draft_id":None, "draft_version":None,
        "draft_modified":False, "draft_loaded_for_generation":False,
        "user_task":decision.user_task.model_dump(mode="json"),
        "legal_user_request":decision.legal_request.model_dump(mode="json"),
        "legal_issues":[], "evidence_pack":None,
        "reasoning":{"status":"DETERMINISTIC_EXACT_LOOKUP", "issues":[]},
        "claims":[], "claim_validation":[], "citations":[],
        "retrieved_sources":exact["sources"],
        "missing_information":[] if found else [answer],
        "warnings":[] if found else [answer],
        "confidence":{"retrieval_quality":{"candidate_count":len(exact["sources"]), "assessment":"deterministic_exact_lookup"}},
    }
    if "candidates" in exact:
        result["candidates"] = exact["candidates"]
    if request.debug:
        result["debug"] = {
            **exact.get("debug", {}),
            "current_message":request.question,
            "active_case_id_before":context.prompt_context.get("turn_before", {}).get("case_id"),
            "active_draft_id_before":context.prompt_context.get("turn_before", {}).get("draft_id"),
            "intent":turn.intent.value, "case_action":turn.case_action.value,
            "draft_action":turn.draft_action.value, "retrieval_required":turn.retrieval_required,
            "legal_issue_analyzer_called":False,
            "primary_goal":"EXACT_REFERENCE_QUERY",
            "llm_orchestrator_called":decision.llm_orchestrator_called,
            "tool_calls":[call.model_dump(mode="json") for call in decision.user_task.tool_calls],
            "active_case_id_after":str(context.conversation.current_case_id) if context.conversation.current_case_id else None,
            "active_draft_id_after":str(context.conversation.current_draft_id) if context.conversation.current_draft_id else None,
            "draft_modified":False, "draft_loaded_for_generation":False,
            "draft_created":False, "draft_id":None, "response_mode":"EXACT_REFERENCE",
            "reasoning_llm_called":False, "claim_validation_called":False,
            "final_response_type":"EXACT_REFERENCE_QUERY",
        }
    return _finalize_agent_response(
        result,
        decision,
        sources=exact["sources"],
        follow_up_needed=not found,
    )


def _conversation_only_response(session, tenant_id, request, provider, context, memory, decision):
    added_facts = []
    turn = decision.conversation_decision
    if decision.user_task.primary_goal == LegalIntent.NEW_CASE_FACT:
        added_facts = memory.add_case_facts(
            context.case_state,
            decision.user_task.new_case_facts or [request.question],
        )
        if context.prompt_context.get("case_state") is not None:
            context.prompt_context["case_state"]["facts"] = context.case_state.facts[-30:]
    if turn.intent.value == "GENERAL_CHAT":
        folded = request.question.strip().casefold()
        if folded.startswith("merci"):
            answer = {"fr":"Avec plaisir.", "ar":"على الرحب والسعة.", "en":"You're welcome."}[request.language]
        elif folded in {"d'accord", "d’accord", "parfait", "ok", "okay"}:
            answer = {"fr":"Très bien.", "ar":"حسنًا.", "en":"All right."}[request.language]
        else:
            answer = {"fr":"Bonjour ! Comment puis-je vous aider ?", "ar":"مرحباً! كيف يمكنني مساعدتك؟", "en":"Hello! How can I help?"}[request.language]
        llm_called = False
    elif provider and hasattr(provider, "generate_conversation"):
        chat_context = {
            "recent_turns":[
                {"role":item["role"], "content":item["content"][:500]}
                for item in context.prompt_context.get("recent_turns", [])[-4:]
            ],
            "current_focus":context.prompt_context.get("current_focus"),
        }
        raw = provider.generate_conversation(request, chat_context)
        reply = ConversationReply.model_validate_json(raw)
        answer = reply.answer
        context.conversation.current_focus = reply.current_focus or context.conversation.current_focus
        llm_called = True
    else:
        answer = "Le fait communiqué a été ajouté au dossier actif. Son incidence doit être confrontée aux autres faits, aux pièces et aux questions juridiques déjà identifiées."
        llm_called = False
    memory.add_turn(
        session, tenant_id, context.conversation, "assistant", answer,
        {"primary_goal":decision.user_task.primary_goal.value, "facts_added":[item["id"] for item in added_facts]},
    )
    session.commit()
    result = {
        "answer":answer, "mode":"generated" if llm_called else "deterministic_chat",
        "response_mode":turn.response_mode.value,
        "intent":turn.intent.value, "conversation_decision":turn.model_dump(mode="json"),
        "answer_kind":"CHAT" if turn.response_mode.value == "CHAT" else "ANALYSE_IA",
        "primary_intent":decision.legal_request.primary_intent.value,
        "primary_goal":decision.user_task.primary_goal.value,
        "secondary_goals":[goal.value for goal in decision.user_task.secondary_goals],
        "conversation_id":str(context.conversation.id),
        "draft_created":False, "draft_id":str(context.active_draft.id) if context.active_draft else None,
        "draft_version":context.active_draft.version if context.active_draft else None,
        "draft_modified":False, "draft_loaded_for_generation":False,
        "case_state_updated":bool(added_facts), "facts_added":added_facts,
        "user_task":decision.user_task.model_dump(mode="json"),
        "legal_user_request":decision.legal_request.model_dump(mode="json"),
        "legal_issues":[], "evidence_pack":None,
        "reasoning":{"status":"CONVERSATIONAL_RESPONSE", "issues":[]},
        "claims":[], "claim_validation":[], "citations":[], "retrieved_sources":[],
        "missing_information":[], "warnings":[], "confidence":{},
        **({"debug":{
            "current_message":request.question,
            "active_case_id_before":context.prompt_context.get("turn_before", {}).get("case_id"),
            "active_draft_id_before":context.prompt_context.get("turn_before", {}).get("draft_id"),
            "intent":turn.intent.value, "case_action":turn.case_action.value,
            "draft_action":turn.draft_action.value,
            "retrieval_required":False, "legal_issue_analyzer_called":False,
            "primary_goal":decision.user_task.primary_goal.value,
            "llm_orchestrator_called":decision.llm_orchestrator_called,
            "tool_calls":[call.model_dump(mode="json") for call in decision.user_task.tool_calls],
            "reasoning_llm_called":llm_called,
            "active_case_id_after":str(context.conversation.current_case_id) if context.conversation.current_case_id else None,
            "active_draft_id_after":str(context.conversation.current_draft_id) if context.conversation.current_draft_id else None,
            "draft_modified":False, "draft_loaded_for_generation":False,
            "response_mode":turn.response_mode.value, "draft_created":False,
            "draft_id":str(context.active_draft.id) if context.active_draft else None,
            "claim_validation_called":False, "final_response_type":decision.user_task.primary_goal.value,
        }} if request.debug else {}),
    }
    return _finalize_agent_response(
        result,
        decision,
        case_updates=added_facts,
        follow_up_needed=False,
    )


def _enrich_pack_from_case_state(pack, state):
    if not state:
        return pack
    facts = [CaseFact(
        id=item.get("id", f"FACT-{index}"), text=item.get("text", ""),
        status=item.get("status", "user_provided"), evidence_ids=item.get("evidence_ids", []),
    ) for index, item in enumerate(state.facts, 1) if item.get("text")]
    disputed = [CaseFact(
        id=item.get("id", f"DISPUTED-{index}"), text=item.get("text", ""),
        status=item.get("status", "disputed"), evidence_ids=item.get("evidence_ids", []),
    ) for index, item in enumerate(state.disputed_facts, 1) if item.get("text")]
    timeline = [TimelineEvent(
        id=item.get("id", f"EVENT-{index}"), date=item.get("date"),
        description=item.get("description", item.get("text", "")),
        evidence_ids=item.get("evidence_ids", []),
    ) for index, item in enumerate(state.timeline, 1) if item.get("description") or item.get("text")]
    return pack.model_copy(update={"case_facts":facts, "disputed_facts":disputed, "timeline":timeline})


def _revise_draft_response_v2(session, tenant_id, request, provider, context, memory, decision, pack, sources, warnings, trace):
    draft = context.active_draft
    operation = decision.user_task.draft_operation.value if decision.user_task.draft_operation else "REWRITE"
    target = decision.user_task.target_section
    if not target and operation == "DELETE" and "dernier" in request.question.casefold():
        target = "__LAST__"
    if not target:
        target = context.conversation.last_modified_section
    resolved_index = resolve_draft_section(draft.sections, target, request.question)
    target_record = draft.sections[resolved_index] if resolved_index is not None else None
    if target_record is not None:
        target = target_record.get("title")
    full_rewrite_allowed = operation == "REWRITE" and bool(decision.user_task.requested_document_type)
    edit_context = {
        "conversation_focus":context.prompt_context.get("conversation_focus"),
        "case_state":context.prompt_context.get("case_state"),
        "active_draft":{
            "draft_id":str(draft.id), "title":draft.title, "document_type":draft.document_type,
            "version":draft.version,
            "section_outline":[item.get("title") for item in draft.sections],
            "section_summaries":[{
                "title":item.get("title"),
                "excerpt":item.get("content", "")[:700],
            } for item in draft.sections],
            "target_section":target_record,
            **({"full_content":draft.content, "sections":draft.sections} if full_rewrite_allowed else {}),
        },
    }
    patch = None
    error = None
    modified_section = None
    displayed = None
    attempts = 0
    if provider and hasattr(provider, "revise_legal_draft"):
        task = decision.user_task.model_copy(update={"target_section":target})
        for attempt in range(2):
            attempts += 1
            try:
                if attempt == 0:
                    raw = provider.revise_legal_draft(request, task, edit_context, sources)
                elif hasattr(provider, "repair_draft_edit"):
                    raw = provider.repair_draft_edit(
                        request, task, edit_context, sources, error or "patch invalide"
                    )
                else:
                    repair_context = {**edit_context, "previous_attempt":{
                        "result":"invalid",
                        "validation_feedback":error or "patch invalide",
                    }}
                    raw = provider.revise_legal_draft(request, task, repair_context, sources)
                candidate = DraftEditOutput.model_validate_json(raw)
                text_to_validate = (
                    candidate.full_rewrite.content if candidate.full_rewrite
                    else candidate.replacement_content or ""
                )
                _validate_revision_references(draft.content, text_to_validate, sources)
                source_ids = [str(source.id) for source in sources]
                draft, modified_section, displayed = DraftManager().apply_patch(
                    session, tenant_id, context.conversation, draft, candidate,
                    operation, request.question, source_ids,
                )
                patch = candidate
                error = None
                break
            except (ProviderError, ValidationError, CitationError, ConversationScopeError) as exc:
                error = str(exc)
                patch = None
    else:
        error = "draft_model_unavailable"

    if patch is not None:
        if decision.user_task.requested_document_type:
            draft.document_type = decision.user_task.requested_document_type
        answer = patch.explanation + "\n\n" + displayed
        warnings.append("Brouillon modifié. Cette version reste à relire par l'avocat.")
    else:
        answer = humanize_draft_failure(error, request.question, request.language)
    warnings = sanitize_user_messages(warnings, request.language)
    memory.add_turn(
        session, tenant_id, context.conversation, "assistant", answer,
        {"primary_goal":"DRAFT_EDIT", "draft_id":str(draft.id), "draft_version":draft.version},
    )
    session.add(AuditEvent(
        tenant_id=tenant_id, action="draft_revision",
        details={"question_sha256":hashlib.sha256(request.question.encode()).hexdigest(),
                 "draft_id":str(draft.id), "version":draft.version, "operation":operation,
                 "success":patch is not None, "target_section":target},
    ))
    session.commit()
    turn = decision.conversation_decision
    result = {
        "answer":answer, "mode":"draft_revised" if patch else "draft_unchanged",
        "response_mode":"DRAFT_EDIT", "answer_kind":"BROUILLON_JURIDIQUE",
        "intent":turn.intent.value, "conversation_decision":turn.model_dump(mode="json"),
        "primary_intent":"DRAFT_EDIT", "primary_goal":"DRAFT_EDIT",
        "secondary_goals":[goal.value for goal in decision.user_task.secondary_goals],
        "conversation_id":str(context.conversation.id), "draft_created":False,
        "draft_modified":patch is not None, "draft_loaded_for_generation":True,
        "draft_id":str(draft.id), "draft_version":draft.version,
        "modified_section":modified_section, "draft_operation":operation,
        "user_task":decision.user_task.model_dump(mode="json"),
        "legal_user_request":decision.legal_request.model_dump(mode="json"),
        "legal_issues":[issue.model_dump(mode="json") for issue in pack.legal_issues if issue.title.strip(" -")],
        "evidence_pack":pack.model_dump(mode="json"),
        "reasoning":{"status":"TARGETED_DRAFT_REVISION" if patch else "DRAFT_REVISION_REJECTED", "issues":[]},
        "claims":[], "claim_validation":[], "citations":[], "retrieved_sources":sources,
        "missing_information":sanitize_user_messages(pack.missing_information, request.language),
        "warnings":list(dict.fromkeys(warnings)), "confidence":{},
        **({"debug":{
            **trace, "current_message":request.question,
            "active_case_id_before":str(context.conversation.current_case_id) if context.conversation.current_case_id else None,
            "active_draft_id_before":str(draft.id), "intent":turn.intent.value,
            "case_action":turn.case_action.value, "draft_action":turn.draft_action.value,
            "retrieval_required":turn.retrieval_required, "legal_issue_analyzer_called":True,
            "reasoning_llm_called":bool(provider), "active_case_id_after":str(context.conversation.current_case_id) if context.conversation.current_case_id else None,
            "active_draft_id_after":str(draft.id), "draft_modified":patch is not None,
            "draft_loaded_for_generation":True, "response_mode":"DRAFT_EDIT",
            "llm_orchestrator_called":decision.llm_orchestrator_called,
            "tool_calls":[call.model_dump(mode="json") for call in decision.user_task.tool_calls],
            "draft_created":False, "draft_id":str(draft.id), "draft_version":draft.version,
            "claim_validation_called":True, "final_response_type":"DRAFT_EDIT",
            "revision_attempts":attempts, "self_repair_attempted":attempts > 1,
            "revision_error_type":("draft_revision_failed" if error else None),
        }} if request.debug else {}),
    }
    return _finalize_agent_response(
        result,
        decision,
        draft_updates=([{
            "draft_id":str(draft.id), "version":draft.version,
            "action":"modified", "section":modified_section,
        }] if patch is not None else []),
        sources=sources,
        follow_up_needed=patch is None,
    )


def _validate_revision_references(old_content, new_content, sources):
    import re
    pattern = re.compile(r"\b(?:article|art\.)\s*([0-9]+(?:\s*(?:bis|ter|quater))?)", re.I)
    known = {value.casefold() for value in pattern.findall(old_content)}
    known.update(str(source.article_number).casefold() for source in sources if source.article_number)
    introduced = {value.casefold() for value in pattern.findall(new_content)} - known
    if introduced:
        raise CitationError("La révision introduit une référence juridique non vérifiée : " + ", ".join(sorted(introduced)))


def _with_conversation_context(session, tenant_id, request, latest):
    retrieval_request = request
    if request.case_id:
        from app.services.case_analysis import snapshot_documents
        memory = latest(session, tenant_id, request.case_id)
        if memory and memory.report and memory.snapshot == snapshot_documents(session, tenant_id, request.case_id):
            issues = memory.report.get("memory", {}).get("legal_issues", [])
            if issues and len(request.question.split()) < 16:
                retrieval_request = request.model_copy(update={
                    "question": request.question + " " + " ".join(item["question"] for item in issues)[:1800]
                })
    if request.previous_questions and len(request.question.split()) < 9 and retrieval_request is request:
        retrieval_request = request.model_copy(update={
            "question": request.previous_questions[-1] + " " + request.question
        })
    return retrieval_request


def _bounded_context(sources, pack):
    explicit_ids = {item.source_id for item in pack.explicit_references}
    by_id = {str(source.id):source for source in sources}
    issue_source_ids = [
        [evidence_id.removeprefix("EVID-") for evidence_id in issue.evidence_ids]
        for issue in pack.legal_issues
    ]
    round_robin=[]
    for position in range(max((len(items) for items in issue_source_ids), default=0)):
        for items in issue_source_ids:
            if position < len(items) and items[position] in by_id:
                round_robin.append(items[position])
    ordered_ids=list(dict.fromkeys([*explicit_ids, *round_robin, *by_id]))
    ordered = [by_id[source_id] for source_id in ordered_ids if source_id in by_id]
    budget = 10000
    context = []
    for source in ordered:
        if budget < 200:
            break
        excerpt = source.original_text[:min(3500, budget)]
        context.append(source.model_copy(update={"original_text": excerpt}))
        budget -= len(excerpt)
    return context


def _deduplicate_claims(claims):
    """Render a heading once while preserving every validated paragraph beneath it."""
    positions = {}
    result = []
    for claim in claims:
        key = " ".join(claim.get("section", "").split()).casefold()
        if not key:
            continue
        if key not in positions:
            positions[key] = len(result)
            result.append(dict(claim))
            continue
        existing = result[positions[key]]
        paragraph = claim.get("text", "").strip()
        current = existing.get("text", "").strip()
        if paragraph and paragraph not in current:
            existing["text"] = current + "\n\n" + paragraph
        citations = existing.setdefault("citations", [])
        known = {(item.get("source_id"), item.get("quote")) for item in citations}
        for citation in claim.get("citations", []):
            identity = (citation.get("source_id"), citation.get("quote"))
            if identity not in known:
                citations.append(citation);known.add(identity)
        if existing.get("grounding_type") != claim.get("grounding_type"):
            existing["grounding_type"] = "GENERAL_REASONING"
    return result


def _answer_kind(intent):
    if intent == LegalIntent.LEGAL_DRAFTING:
        return "BROUILLON_JURIDIQUE"
    if intent == LegalIntent.LEGAL_OPINION:
        return "PROJET_AVIS_JURIDIQUE"
    return "ANALYSE_IA"
