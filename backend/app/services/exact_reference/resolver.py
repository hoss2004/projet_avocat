from dataclasses import dataclass

from app.models.legal import LegalDocument
from app.services.exact_reference.normalization import normalize_document_reference


@dataclass(frozen=True)
class DocumentResolution:
    document_id: object | None
    requested_document: str | None
    candidates: list[LegalDocument]
    status: str
    matched_alias_id: object | None = None
    normalized_document_reference: str = ""


@dataclass(frozen=True)
class _DocumentMatch:
    document: LegalDocument
    reference: str
    alias_id: object | None = None


class LegalDocumentResolver:
    def resolve(self, repository, requested_document: str | None, explicit_document_id=None, query_text: str | None = None) -> DocumentResolution:
        if explicit_document_id:
            document = repository.get(LegalDocument, explicit_document_id)
            return DocumentResolution(
                document_id=document.id if document else None,
                requested_document=requested_document,
                candidates=[document] if document else [],
                status="FOUND" if document else "DOCUMENT_NOT_FOUND",
                normalized_document_reference=normalize_document_reference(requested_document),
            )

        if not requested_document and query_text:
            matches = self._documents_mentioned_in_query(repository, query_text)
            if len(matches) == 1:
                return self._resolution(matches[0], "FOUND")
            if len(matches) > 1:
                return DocumentResolution(
                    None,
                    None,
                    [match.document for match in matches],
                    "AMBIGUOUS_REFERENCE",
                )

        if not requested_document:
            return DocumentResolution(None, None, [], "UNSPECIFIED")

        needle = normalize_document_reference(requested_document)
        matches = self._alias_matches(repository, needle, exact_reference=True)
        if not matches:
            partial_matches = []
            exact_matches = []
            for document in repository.documents(limit=200):
                labels = self._labels(document)
                match = _DocumentMatch(document, self._title(document))
                if needle in labels:
                    exact_matches.append(match)
                elif any(needle and (needle in label or label in needle) for label in labels):
                    partial_matches.append(match)
            matches = exact_matches or partial_matches

        if len(matches) == 1:
            return self._resolution(matches[0], "FOUND", requested_document=requested_document)
        if len(matches) > 1:
            return DocumentResolution(
                None,
                requested_document,
                [match.document for match in matches],
                "AMBIGUOUS_REFERENCE",
                normalized_document_reference=needle,
            )
        return DocumentResolution(
            None,
            requested_document,
            [],
            "DOCUMENT_NOT_FOUND",
            normalized_document_reference=needle,
        )

    def _resolution(self, match: _DocumentMatch, status: str, requested_document: str | None = None):
        return DocumentResolution(
            document_id=match.document.id,
            requested_document=requested_document or match.reference,
            candidates=[match.document],
            status=status,
            matched_alias_id=match.alias_id,
            normalized_document_reference=normalize_document_reference(match.reference),
        )

    def _documents_mentioned_in_query(self, repository, query_text: str):
        haystack = normalize_document_reference(query_text)
        alias_matches = self._alias_matches(repository, haystack)
        if alias_matches:
            return alias_matches
        matches = []
        for document in repository.documents(limit=200):
            labels = self._labels(document)
            if any(len(label) >= 4 and label in haystack for label in labels):
                matches.append(_DocumentMatch(document, self._title(document)))
        return matches

    def _labels(self, document):
        labels = {
            normalize_document_reference(document.title_ar),
            normalize_document_reference(document.title_fr),
            normalize_document_reference(document.filename),
            normalize_document_reference(document.source_name),
        }
        labels.discard("")
        return labels

    def _alias_matches(self, repository, normalized_text: str, *, exact_reference: bool = False):
        aliases = repository.document_aliases() if hasattr(repository, "document_aliases") else []
        exact = {}
        contained = {}
        for alias in aliases:
            value = alias.normalized_alias or normalize_document_reference(alias.alias)
            if not value or len(value) < 4:
                continue
            is_exact = value == normalized_text
            is_contained = value in normalized_text or (exact_reference and normalized_text in value)
            if not (is_exact or is_contained):
                continue
            document = repository.get(LegalDocument, alias.document_id)
            if not document:
                continue
            target = exact if is_exact else contained
            current = target.get(document.id)
            match = _DocumentMatch(document, alias.alias, alias.id)
            if current is None or len(value) > len(normalize_document_reference(current.reference)):
                target[document.id] = match
        selected = exact or contained
        return [selected[key] for key in sorted(selected, key=str)]

    def _title(self, document):
        return document.title_ar or document.title_fr or document.filename
