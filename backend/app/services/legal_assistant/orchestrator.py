from dataclasses import dataclass
import inspect
import re

from pydantic import ValidationError

from app.services.embeddings.providers import ProviderError
from app.services.legal_assistant.intent import LegalIntentDecision, LegalIntentRouter
from app.services.legal_assistant.models import (
    CaseAction,
    ChatPlanningOutput,
    ConversationDecision,
    ConversationIntent,
    DraftAction,
    DraftOperation,
    LegalIntent,
    LegalToolCall,
    LegalToolName,
    ResponseMode,
    UserTask,
)


@dataclass(frozen=True)
class LegalChatDecision:
    user_task: UserTask
    legal_request: object
    exact_reference_query: object | None
    llm_orchestrator_called: bool
    conversation_decision: ConversationDecision
    planning_error: str | None = None


class LegalChatOrchestrator:
    """Understand a chat message first, then expose an explicit internal tool plan."""

    def understand(
        self,
        request,
        repository,
        provider,
        seed: LegalIntentDecision | None = None,
        prompt_context=None,
        max_tool_calls=8,
    ):
        seed = seed or LegalIntentRouter().route(request, repository=repository)
        prompt_context = prompt_context or {}
        planning_request = request
        planning_context = prompt_context
        self_contained = self._is_self_contained(request.question)
        # A long, self-contained instruction starts a new legal task. Feeding the
        # previous long prompt to a small local planner can make it copy an old
        # test case into the new retrieval query.
        if self_contained:
            planning_request = request.model_copy(update={"previous_questions": []})
            planning_context = {
                **prompt_context,
                "recent_turns": [],
                "pending_questions": [],
            }
        plan = None
        called = False
        planning_error = None
        if provider is not None and hasattr(provider, "plan_legal_chat") and not self_contained:
            try:
                called = True
                planner = provider.plan_legal_chat
                if len(inspect.signature(planner).parameters) >= 3:
                    raw = planner(planning_request, seed.user_request, planning_context)
                else:
                    raw = planner(planning_request, seed.user_request)
                plan = ChatPlanningOutput.model_validate_json(raw)
            except (ProviderError, ValidationError, ValueError) as exc:
                planning_error = str(exc)

        if plan is None:
            plan = self._fallback_plan(request, seed, prompt_context)

        if self._is_general_chat(request.question):
            plan = plan.model_copy(update={
                "primary_goal":LegalIntent.SIMPLE_CHAT,
                "secondary_goals":[], "issue_queries":[],
                "needs_legal_research":False, "needs_case_law":False,
                "needs_case_documents":False, "needs_active_draft":False,
                "needs_case_context":False, "draft_operation":None,
                "target_section":None, "new_case_facts":[],
                "create_new_case":False,
                "response_strategy":"Répondre simplement au message actuel.",
            })

        primary = self._guard_primary_goal(request, seed, plan.primary_goal, prompt_context)
        secondary = list(dict.fromkeys(
            goal for goal in [*plan.secondary_goals, seed.user_request.primary_intent]
            if goal != primary and goal != LegalIntent.EXACT_REFERENCE_QUERY
        ))
        issue_queries = [
            query.strip() for query in plan.issue_queries
            if len(query.strip()) >= 2
            and re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ\u0600-\u06ff]", query)
        ]
        issue_queries = self._ground_issue_queries(request.question, primary, issue_queries)
        if not issue_queries and plan.needs_legal_research:
            issue_queries = [request.question]
        tool_calls = self._tool_calls(request, seed, plan, issue_queries)[:max_tool_calls]
        legal_request = seed.user_request.model_copy(update={
            "primary_intent": primary,
            "secondary_goals": secondary,
            "requested_output": self._requested_output(primary),
        })
        active_draft = prompt_context.get("active_draft") or {}
        user_task = UserTask(
            primary_goal=primary,
            secondary_goals=secondary,
            explicit_references=seed.user_request.explicit_references,
            requested_document_type=plan.requested_document_type,
            requested_transformations=plan.requested_transformations,
            language=request.language,
            case_id=request.case_id,
            draft_id=active_draft.get("draft_id"),
            draft_operation=plan.draft_operation,
            target_section=plan.target_section,
            new_case_facts=plan.new_case_facts,
            create_new_case=plan.create_new_case,
            case_title=plan.case_title,
            uploaded_documents=seed.user_request.uploaded_documents,
            mentioned_documents=plan.mentioned_documents,
            constraints=seed.user_request.constraints,
            issue_queries=issue_queries,
            tool_calls=tool_calls,
            response_strategy=plan.response_strategy,
            original_message=request.question,
        )
        conversation_decision = self._conversation_decision(primary, plan, request, prompt_context)
        return LegalChatDecision(
            user_task=user_task,
            legal_request=legal_request,
            exact_reference_query=seed.exact_reference_query,
            llm_orchestrator_called=called,
            conversation_decision=conversation_decision,
            planning_error=planning_error,
        )

    def _guard_primary_goal(self, request, seed, planned, prompt_context):
        if planned == LegalIntent.EXACT_REFERENCE_QUERY:
            return seed.user_request.primary_intent
        if planned == LegalIntent.DRAFT_EDIT:
            if not prompt_context.get("active_draft"):
                return seed.user_request.primary_intent
            edit_signal = self._draft_operation(request.question.casefold()) or re.search(
                r"\b(?:reprends?|continue|ça.*pas d['’]accord|ce passage|cette partie)\b",
                request.question.casefold(),
            )
            if not edit_signal:
                return seed.user_request.primary_intent
        selected = getattr(request, "mode", "LEGAL_RESEARCH")
        if (
            seed.user_request.primary_intent == LegalIntent.LEGAL_OPINION
            and planned == LegalIntent.LEGAL_DRAFTING
            and re.search(r"\b(?:avis|consultation|opinion|analyse) juridique\b", request.question, re.I)
        ):
            return LegalIntent.LEGAL_OPINION
        if selected not in {"LEGAL_RESEARCH", "QUICK_ANSWER"} and planned == LegalIntent.LEGAL_RESEARCH:
            return seed.user_request.primary_intent
        return planned

    def _is_self_contained(self, question):
        words = re.findall(r"[\w\u0600-\u06ff]+", question)
        return len(words) >= 90 and bool(re.search(
            r"\b(?:avis juridique|consultation juridique|analyse (?:compl[èe]te|approfondie)|"
            r"documents? disponibles?|les faits|le contrat)\b",
            question,
            re.I,
        ))

    def _ground_issue_queries(self, question, primary, queries):
        """Keep retrieval on the current message and decompose common opinions."""
        folded = question.casefold()
        if primary == LegalIntent.LEGAL_OPINION and re.search(
            r"\b(?:contrat|contractuel|obligation|inex[ée]cution|r[ée]siliation|mise en demeure)\b",
            folded,
        ):
            return [
                "force obligatoire bonne foi contractuelle obligations contractuelles exécution du contrat",
                "inexécution contractuelle manquement grave mise en demeure résolution résiliation dommages échéance retard du débiteur exécution forcée",
                "obligations réciproques exception d'inexécution empêchement du créancier factures impayées responsabilité contractuelle causalité preuve",
            ]
        stale_markers = re.compile(
            r"(?:^|\n)\s*(?:system|user|sources? juridiques? de test|source\s+\d+)\b|test-\d+",
            re.I,
        )
        current_words = set(re.findall(r"[a-zà-öø-ÿ\u0600-\u06ff]{4,}", folded))
        grounded = []
        for query in queries:
            if stale_markers.search(query):
                continue
            query_words = set(re.findall(r"[a-zà-öø-ÿ\u0600-\u06ff]{4,}", query.casefold()))
            if len(query) > 700 and query_words and len(query_words & current_words) / len(query_words) < 0.55:
                continue
            grounded.append(query[:700])
        return grounded

    def _tool_calls(self, request, seed, plan, issue_queries):
        calls = []
        if seed.user_request.explicit_references:
            calls.append(LegalToolCall(
                name=LegalToolName.LOOKUP_EXACT_REFERENCE,
                query=request.question,
                reason="Récupérer les références explicitement invoquées avant le raisonnement.",
            ))
        if plan.needs_legal_research:
            calls.extend(
                LegalToolCall(
                    name=LegalToolName.SEARCH_LEGAL_SOURCES,
                    query=query,
                    reason="Vérifier le droit positif pertinent pour cette question.",
                    issue_id=f"ISSUE-{index}",
                )
                for index, query in enumerate(issue_queries, 1)
            )
        if plan.needs_case_law:
            calls.append(LegalToolCall(
                name=LegalToolName.SEARCH_CASE_LAW,
                query=" ; ".join(issue_queries),
                reason="Rechercher une jurisprudence présente dans le corpus.",
            ))
        if plan.needs_case_documents and request.case_id:
            calls.append(LegalToolCall(
                name=LegalToolName.SEARCH_CASE_DOCUMENTS,
                query=" ; ".join(issue_queries) or request.question,
                reason="Rechercher les faits et pièces dans le dossier sélectionné.",
            ))
        if plan.needs_active_draft:
            calls.append(LegalToolCall(
                name=LegalToolName.GET_ACTIVE_DRAFT,
                reason="Charger le brouillon actif avant une transformation conversationnelle.",
            ))
        if plan.needs_case_context and request.case_id:
            calls.append(LegalToolCall(
                name=LegalToolName.GET_CASE_CONTEXT,
                reason="Charger l'état structuré du dossier actif.",
            ))
        if plan.new_case_facts and request.case_id:
            calls.append(LegalToolCall(
                name=LegalToolName.UPDATE_CASE_STATE,
                reason="Mémoriser les nouveaux faits communiqués dans le dossier actif.",
            ))
        return calls

    def _fallback_plan(self, request, seed, prompt_context):
        question = request.question.strip()
        folded = question.casefold()
        active_draft = prompt_context.get("active_draft")
        if self._is_general_chat(question):
            return ChatPlanningOutput(
                primary_goal=LegalIntent.SIMPLE_CHAT,
                issue_queries=[],
                needs_legal_research=False,
                response_strategy="Répondre naturellement et brièvement dans la langue de l'utilisateur.",
            )
        operation = self._draft_operation(folded) if active_draft else None
        if operation:
            needs_law = operation == DraftOperation.ADD_SOURCE or bool(seed.user_request.explicit_references)
            return ChatPlanningOutput(
                primary_goal=LegalIntent.DRAFT_EDIT,
                secondary_goals=[seed.user_request.primary_intent],
                issue_queries=[question] if needs_law else [],
                requested_document_type="lettre_client" if "lettre" in folded or "courrier" in folded else None,
                requested_transformations=[operation.value],
                needs_legal_research=needs_law,
                needs_active_draft=True,
                needs_case_context=bool(request.case_id),
                draft_operation=operation,
                target_section=self._target_section(question, prompt_context),
                response_strategy="Modifier uniquement la partie visée du brouillon actif et préserver le reste.",
            )
        new_fact = bool(re.search(
            r"\b(?:j['’]ai oublié|nouveau fait|ajoute ce fait|il existe également|fait supplémentaire|"
            r"nous avons retrouv[ée]|on a retrouv[ée]|documents? retrouv[ée]s?)\b",
            folded,
        ))
        if new_fact and request.case_id:
            return ChatPlanningOutput(
                primary_goal=LegalIntent.NEW_CASE_FACT,
                secondary_goals=[LegalIntent.CASE_CHAT],
                issue_queries=[],
                needs_legal_research=False,
                needs_case_context=True,
                new_case_facts=[question],
                response_strategy="Mémoriser le nouveau fait comme information communiquée et expliquer prudemment son impact possible.",
            )
        if not request.case_id and re.search(r"\b(?:nouveau dossier|nouvelle affaire|voici mon dossier)\b", folded):
            return ChatPlanningOutput(
                primary_goal=seed.user_request.primary_intent,
                issue_queries=[question],
                needs_legal_research=True,
                needs_case_context=True,
                create_new_case=True,
                case_title=question[:120],
                response_strategy="Créer un dossier distinct, structurer les faits puis analyser la demande.",
            )
        intent = seed.user_request.primary_intent
        return ChatPlanningOutput(
            primary_goal=intent,
            secondary_goals=[],
            issue_queries=[request.question],
            needs_legal_research=intent not in {LegalIntent.CASE_CHAT, LegalIntent.SIMPLE_CHAT},
            needs_case_documents=bool(request.case_id),
            needs_case_context=bool(request.case_id),
            response_strategy="Répondre de façon conversationnelle, structurée et prudente.",
        )

    def _draft_operation(self, question):
        patterns = (
            (DraftOperation.DELETE, r"\b(?:supprime|retire|enlève|enleve)\b"),
            (DraftOperation.EXPAND, r"\b(?:développe|developpe|approfondis|détaille|detaille)\b"),
            (DraftOperation.SHORTEN, r"\b(?:raccourcis|résume|resume|plus court)\b"),
            (DraftOperation.MOVE, r"\b(?:déplace|deplace)\b"),
            (DraftOperation.MERGE, r"\b(?:fusionne|regroupe)\b"),
            (DraftOperation.SOFTEN_CONCLUSION, r"\b(?:moins affirmati|trop affirmati|nuance|plus prudent|adoucis)\w*"),
            (DraftOperation.STRENGTHEN_ARGUMENT, r"\b(?:renforce|plus convaincant|trop faible)\b"),
            (DraftOperation.ADD_COUNTERARGUMENT, r"\b(?:contre-argument|partie adverse|arguments? adverse)\b"),
            (DraftOperation.REMOVE_ARGUMENT, r"\b(?:supprime .*argument|ne .*retenir)\b"),
            (DraftOperation.CHANGE_TONE, r"\b(?:plus objectif|plus formel|change .*ton|ton plus|plus clair)\b"),
            (DraftOperation.ADD_SOURCE, r"\b(?:utilise|ajoute|intègre|integre).*(?:article|source)\b"),
            (DraftOperation.UPDATE_WITH_NEW_FACTS, r"\b(?:mets? à jour|met a jour|nouveau fait)\b"),
            (DraftOperation.REWRITE, r"\b(?:réécris|reecris|réécrire|reecrire|refais|transforme|pas d['’]accord)\b"),
            (DraftOperation.ADD, r"\b(?:ajoute|insère|insere|intègre|integre)\b"),
            (DraftOperation.REPLACE, r"\b(?:remplace|corrige)\b"),
        )
        for operation, pattern in patterns:
            if re.search(pattern, question):
                return operation
        return None

    def _target_section(self, question, prompt_context):
        folded = question.casefold()
        if "conclusion" in folded:
            return "Conclusion"
        ordinal = re.search(
            r"\b(?:la |le )?(premi(?:ère|ere)|deuxi(?:ème|eme)|troisi(?:ème|eme)|quatri(?:ème|eme))\b",
            folded,
        )
        sections = (prompt_context.get("active_draft") or {}).get("sections", [])
        if ordinal and sections:
            index = {
                "première": 0, "premiere": 0, "deuxième": 1, "deuxieme": 1,
                "troisième": 2, "troisieme": 2, "quatrième": 3, "quatrieme": 3,
            }.get(ordinal.group(1))
            if index is not None and index < len(sections):
                return sections[index].get("title")
        topic = re.search(
            r"(?:partie|section|passage|paragraphe|argument)\s+(?:uniquement\s+)?(?:sur|concernant|relati(?:f|ve)\s+[àa]|de)\s+(.+?)(?:[.!?]|$)",
            folded,
        )
        if topic:
            return topic.group(1).strip()
        return prompt_context.get("last_modified_section")

    def _is_general_chat(self, message):
        return bool(re.fullmatch(
            r"\s*(?:bonjour|bonsoir|salut|merci(?: beaucoup)?|d['’]accord|parfait|ok|okay|hello|hi)[\s.!?]*",
            message.casefold(),
        ))

    def _conversation_decision(self, primary, plan, request, prompt_context):
        if primary == LegalIntent.SIMPLE_CHAT:
            return ConversationDecision(
                intent=ConversationIntent.GENERAL_CHAT, case_action=CaseAction.NONE,
                draft_action=DraftAction.NONE, retrieval_required=False,
                reasoning_required=True, response_mode=ResponseMode.CHAT,
            )
        if primary == LegalIntent.EXACT_REFERENCE_QUERY:
            return ConversationDecision(
                intent=ConversationIntent.EXACT_REFERENCE_LOOKUP, case_action=CaseAction.KEEP,
                draft_action=DraftAction.NONE, retrieval_required=True,
                reasoning_required=False, response_mode=ResponseMode.EXACT_REFERENCE,
            )
        if plan.create_new_case:
            intent, case_action = ConversationIntent.NEW_CASE, CaseAction.CREATE
        elif primary == LegalIntent.NEW_CASE_FACT:
            intent, case_action = ConversationIntent.UPDATE_CASE_FACTS, CaseAction.UPDATE_FACTS
        elif primary == LegalIntent.DRAFT_EDIT:
            intent, case_action = ConversationIntent.EDIT_DRAFT, CaseAction.KEEP
        elif primary == LegalIntent.LEGAL_OPINION:
            intent, case_action = ConversationIntent.LEGAL_OPINION, CaseAction.KEEP
        elif primary == LegalIntent.LEGAL_DRAFTING:
            intent, case_action = ConversationIntent.CREATE_DRAFT, CaseAction.KEEP
        elif primary == LegalIntent.CASE_ANALYSIS:
            intent, case_action = ConversationIntent.CASE_ANALYSIS, CaseAction.KEEP
        elif primary == LegalIntent.DOCUMENT_ANALYSIS:
            intent, case_action = ConversationIntent.ANALYZE_NEW_DOCUMENT, CaseAction.KEEP
        elif primary == LegalIntent.LEGAL_RESEARCH:
            intent, case_action = ConversationIntent.LEGAL_RESEARCH, CaseAction.KEEP
        else:
            intent, case_action = ConversationIntent.LEGAL_QUESTION, CaseAction.KEEP
        draft_action = (
            DraftAction.EDIT if primary == LegalIntent.DRAFT_EDIT else
            DraftAction.CREATE if primary in {LegalIntent.LEGAL_OPINION, LegalIntent.LEGAL_DRAFTING} else
            DraftAction.NONE
        )
        response_mode = {
            LegalIntent.DRAFT_EDIT:ResponseMode.DRAFT_EDIT,
            LegalIntent.LEGAL_OPINION:ResponseMode.LEGAL_OPINION,
            LegalIntent.LEGAL_DRAFTING:ResponseMode.LEGAL_OPINION,
            LegalIntent.DOCUMENT_ANALYSIS:ResponseMode.DOCUMENT_ANALYSIS,
            LegalIntent.LEGAL_RESEARCH:ResponseMode.LEGAL_RESEARCH,
        }.get(primary, ResponseMode.LEGAL_ANALYSIS)
        return ConversationDecision(
            intent=intent, case_action=case_action, draft_action=draft_action,
            retrieval_required=plan.needs_legal_research or bool(plan.needs_case_law or plan.needs_case_documents),
            reasoning_required=True, response_mode=response_mode,
        )

    def _requested_output(self, intent):
        return {
            LegalIntent.LEGAL_OPINION: "legal_opinion",
            LegalIntent.LEGAL_DRAFTING: "legal_draft",
            LegalIntent.ARGUMENTATION: "arguments_and_counterarguments",
            LegalIntent.PROCEDURAL_ANALYSIS: "procedural_analysis",
            LegalIntent.CASE_ANALYSIS: "case_analysis",
            LegalIntent.CASE_CHAT: "case_follow_up",
            LegalIntent.DOCUMENT_ANALYSIS: "document_analysis",
            LegalIntent.DRAFT_EDIT: "draft_revision",
            LegalIntent.NEW_CASE_FACT: "case_update",
            LegalIntent.SIMPLE_CHAT: "conversation",
        }.get(intent, "legal_research")


class LegalConversationOrchestrator(LegalChatOrchestrator):
    """Public turn-level entrypoint. Kept separate from retrieval and drafting services."""
