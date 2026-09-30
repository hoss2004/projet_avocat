from dataclasses import dataclass
import re

from app.services.exact_reference.router import QueryRouter
from app.services.legal_assistant.models import (
    ExplicitLegalReference,
    LegalIntent,
    LegalUserRequest,
)


@dataclass(frozen=True)
class LegalIntentDecision:
    user_request: LegalUserRequest
    exact_reference_query: object | None


class LegalIntentRouter:
    """Classify the requested outcome while extracting references independently."""

    _signals = (
        (LegalIntent.LEGAL_OPINION, r"\b(?:avis juridique|consultation juridique|opinion juridique|analyse juridique|donne[r-z]*\s+un avis)\b|(?:استشارة قانونية|رأي قانوني|تحليل قانوني)"),
        (LegalIntent.LEGAL_DRAFTING, r"\b(?:brouillon|projet de (?:lettre|requ[êe]te|conclusions?|acte)|r[ée]dig\w* (?:une? )?(?:lettre|requ[êe]te|conclusions?|acte|mise en demeure|m[ée]moire)|m[ée]moire en d[ée]fense)\b|(?:صياغة|حرر|اكتب عريضة)"),
        (LegalIntent.ARGUMENTATION, r"\b(?:arguments?|contre[- ]arguments?|moyens? de d[ée]fense|position adverse|r[ée]fut\w*|comparer? les positions?)\b|(?:الحجج|الحجج المضادة|دفوع)"),
        (LegalIntent.PROCEDURAL_ANALYSIS, r"\b(?:recevabilit[ée]|comp[ée]tence (?:territoriale|mat[ée]rielle)|prescription|voie de recours|d[ée]lai de recours|proc[ée]dure)\b|(?:الاختصاص|الإجراءات|التقادم|الطعن)"),
        (LegalIntent.DOCUMENT_ANALYSIS, r"\b(?:analyse[rz]? (?:ce|le) document|analyse documentaire|examine[rz]? (?:ce|le) (?:contrat|document|rapport))\b|(?:حلل الوثيقة|تحليل الوثيقة)"),
        (LegalIntent.CASE_ANALYSIS, r"\b(?:analyse compl[èe]te du dossier|analyse[rz]? (?:ce|le|mon) dossier|strat[ée]gie (?:du|de) dossier|pr[ée]parer (?:le )?dossier)\b|(?:حلل الملف|تحليل الملف)"),
    )
    _analysis_signal = re.compile(
        r"\b(?:analys\w*|avis|applicab\w*|cons[ée]quences?|responsabilit[ée]|risques?|arguments?|contre[- ]arguments?|r[ée]dig\w*|strat[ée]gie|proc[ée]dur\w*|qualification|interpr[ée]t\w*)\b|(?:حلل|تحليل|رأي|مسؤولية|حجج|صياغة|إجراءات)",
        re.I,
    )
    _documentary_signal = re.compile(
        r"\b(?:affich\w*|retrouv\w*|consult\w*|donne[rz]? (?:moi )?(?:le )?texte|texte (?:exact|original|int[ée]gral)|que dit|contenu de)\b|(?:اعرض|ابحث عن|النص الأصلي|ماذا يقول|ما هو نص)",
        re.I,
    )

    def route(self, request, repository=None) -> LegalIntentDecision:
        exact_route = QueryRouter().route(request, repository=repository)
        exact = exact_route.exact_reference
        references = []
        if exact:
            references = [
                ExplicitLegalReference(
                    raw_text=item.raw_text,
                    article_number=item.normalized_article_number,
                    article_suffix=item.article_suffix,
                    document_reference=item.raw_document_reference,
                    resolved_document_id=item.resolved_document_id,
                )
                for item in exact.references
            ]

        intent = self._primary_intent(request, bool(references), exact is not None)
        output = self._requested_output(intent)
        constraints = []
        if getattr(request, "at_date", None):
            constraints.append(f"law_at_date:{request.at_date.isoformat()}")
        if getattr(request, "document_id", None):
            constraints.append("selected_document_only")
        if getattr(request, "scope", "LEGAL_ONLY") != "LEGAL_ONLY":
            constraints.append(f"scope:{request.scope}")
        user_request = LegalUserRequest(
            primary_intent=intent,
            explicit_references=references,
            requested_output=output,
            language=request.language,
            case_id=request.case_id,
            uploaded_documents=[request.document_id] if request.document_id else [],
            constraints=constraints,
            original_question=request.question,
        )
        return LegalIntentDecision(user_request=user_request, exact_reference_query=exact)

    def _primary_intent(self, request, has_references: bool, has_reference_query: bool) -> LegalIntent:
        mode_map = {
            "LEGAL_OPINION": LegalIntent.LEGAL_OPINION,
            "LEGAL_DRAFTING": LegalIntent.LEGAL_DRAFTING,
            "DRAFT_PREPARATION": LegalIntent.LEGAL_DRAFTING,
            "CASE_ANALYSIS": LegalIntent.CASE_ANALYSIS,
            "DOCUMENT_ANALYSIS": LegalIntent.DOCUMENT_ANALYSIS,
            "ARGUMENTATION": LegalIntent.ARGUMENTATION,
            "COMPARE_ARGUMENTS": LegalIntent.ARGUMENTATION,
            "PROCEDURAL_ANALYSIS": LegalIntent.PROCEDURAL_ANALYSIS,
            "CASE_CHAT": LegalIntent.CASE_CHAT,
        }
        selected = mode_map.get(getattr(request, "mode", "LEGAL_RESEARCH"))
        if selected:
            return selected
        question = request.question
        for intent, pattern in self._signals:
            if re.search(pattern, question, re.I):
                return intent
        if getattr(request, "case_id", None):
            return LegalIntent.CASE_CHAT if getattr(request, "previous_questions", []) else LegalIntent.CASE_ANALYSIS
        if has_reference_query and self._documentary_signal.search(question) and not self._analysis_signal.search(question):
            return LegalIntent.EXACT_REFERENCE_QUERY
        if has_reference_query and not self._analysis_signal.search(question) and self._is_reference_only(question):
            return LegalIntent.EXACT_REFERENCE_QUERY
        return LegalIntent.LEGAL_RESEARCH

    def _is_reference_only(self, question: str) -> bool:
        words = re.findall(r"[\w\u0600-\u06ff]+", question)
        return len(words) <= 10

    def _requested_output(self, intent: LegalIntent) -> str:
        return {
            LegalIntent.EXACT_REFERENCE_QUERY: "original_legal_text",
            LegalIntent.LEGAL_OPINION: "legal_opinion",
            LegalIntent.LEGAL_DRAFTING: "legal_draft",
            LegalIntent.ARGUMENTATION: "arguments_and_counterarguments",
            LegalIntent.PROCEDURAL_ANALYSIS: "procedural_analysis",
            LegalIntent.CASE_ANALYSIS: "case_analysis",
            LegalIntent.CASE_CHAT: "case_follow_up",
            LegalIntent.DOCUMENT_ANALYSIS: "document_analysis",
        }.get(intent, "legal_research")
