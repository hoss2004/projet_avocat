from dataclasses import dataclass
import re

from app.services.exact_reference.parser import ExactReferenceParser, ExactReferenceQuery


@dataclass(frozen=True)
class QueryRoute:
    query_type: str
    exact_reference: ExactReferenceQuery | None = None


class QueryRouter:
    def __init__(self, parser: ExactReferenceParser | None = None):
        self.parser = parser or ExactReferenceParser()

    def route(self, request, repository=None) -> QueryRoute:
        if getattr(request, "scope", "LEGAL_ONLY") == "CASE_ONLY":
            return QueryRoute(query_type="CASE_ANALYSIS" if getattr(request, "case_id", None) else "DOCUMENT_ANALYSIS")
        if self._looks_like_technical_text(request.question):
            return QueryRoute(query_type="CONCEPTUAL_LEGAL_SEARCH")
        known_document_references = ()
        if repository is not None and hasattr(repository, "document_aliases"):
            known_document_references = tuple(alias.alias for alias in repository.document_aliases())
        exact = self.parser.parse(
            request.question,
            language=getattr(request, "language", "fr"),
            scope=getattr(request, "scope", "LEGAL_ONLY"),
            known_document_references=known_document_references,
        )
        if exact:
            return QueryRoute(query_type=exact.query_type, exact_reference=exact)
        if getattr(request, "case_id", None):
            return QueryRoute(query_type="CASE_ANALYSIS")
        return QueryRoute(query_type="CONCEPTUAL_LEGAL_SEARCH")

    def _looks_like_technical_text(self, question: str) -> bool:
        technical = re.search(
            r"(?:```|`[^`]+`|\b\w+(?:_\w+)+\s*=|\b(?:class|def|function|const|let|var|select|insert|update|delete|json|yaml|sql|regex|variable|metadata|field|champ|pseudo[- ]?code)\b|==|:=|=>)",
            question,
            flags=re.I,
        )
        if not technical:
            return False
        legal_intent = re.search(
            r"\b(?:recherch\w*|donn\w*|affich\w*|consult\w*|cit\w*|texte|contenu|que dit|selon|dans)\b|(?:ابحث|اعرض|ما هو|ماذا يقول|الفصل)",
            question,
            flags=re.I,
        )
        return legal_intent is None
