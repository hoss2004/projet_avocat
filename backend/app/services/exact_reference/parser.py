from dataclasses import dataclass, replace
import re

from app.services.exact_reference.normalization import (
    ARTICLE_WORDS,
    SUFFIX_PATTERN,
    normalize_article_number,
    normalize_document_reference,
    normalize_unicode,
)


REFERENCE_BASE = r"[\w\u0600-\u06FF٠-٩۰-۹]+"
REFERENCE_TOKEN = rf"{REFERENCE_BASE}(?:[\s\-–—]+{SUFFIX_PATTERN})?"
LABEL_SEPARATOR = r"\s*(?:[:：]\s*)?(?:(?:n|no|nº|n°|num(?:ero|éro)?)\s*[.:°º-]?\s*)?"
FORWARD = re.compile(
    rf"\b(?P<label>{ARTICLE_WORDS})(?!\w){LABEL_SEPARATOR}[\(\[{{]?\s*(?P<ref>{REFERENCE_TOKEN})\s*[\)\]}}]?",
    re.I,
)
REVERSED = re.compile(
    rf"[\(\[{{]?\s*(?P<ref>[\d٠-٩۰-۹]+(?:[\s\-–—]+{SUFFIX_PATTERN})?)\s*[\)\]}}]?\s+(?P<label>{ARTICLE_WORDS})\b",
    re.I,
)
NUMBER_TOKEN = re.compile(r"[\(\[{{]?\s*(?P<ref>[\d٠-٩۰-۹]+)\s*[\)\]}}]?")
ARTICLE_MARKER = re.compile(rf"\b{ARTICLE_WORDS}(?!\w)", re.I)
REFERENCE_STOPWORDS = {"dans", "de", "du", "des", "from", "of", "من", "et", "ou"}
DOCUMENT_HINT = re.compile(
    r"(?:\b(?:dans|du|de|de la|de l|d'|of|from)\b|من)\s+(?P<document>[^?.!,;،؛\n\r]+)",
    re.I,
)


@dataclass(frozen=True)
class ExactLegalReference:
    raw_text: str
    raw_article_number: str
    normalized_article_number: str
    article_suffix: str | None
    raw_document_reference: str | None
    normalized_document_reference: str
    resolved_document_id: object | None
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class ExactReferenceQuery:
    query_type: str
    references: tuple[ExactLegalReference, ...]
    raw_parser_matches: int
    deduplicated_reference_count: int
    source_type: str
    raw_document_reference: str | None
    raw_reference: str
    article_number: str
    normalized_reference: str
    normalized_article_number: str
    article_suffix: str | None
    requested_document: str | None
    original_question: str
    language: str
    explicit_scope: str
    validation_status: str
    parser_confidence: float
    detected_references: tuple[str, ...]
    parser_failure_reason: str | None = None


@dataclass(frozen=True)
class _ArticleMatch:
    reference: ExactLegalReference
    pattern: str


@dataclass(frozen=True)
class _DocumentMention:
    raw: str
    normalized: str
    start: int
    end: int


class ExactReferenceParser:
    def parse(
        self,
        question: str,
        *,
        language: str = "fr",
        scope: str = "LEGAL_ONLY",
        known_document_references: tuple[str, ...] = (),
    ) -> ExactReferenceQuery | None:
        text = normalize_unicode(question)
        documents = self._document_mentions(text, known_document_references)
        raw_matches = self._article_matches(text)
        if not raw_matches and documents:
            raw_matches = self._shorthand_matches(text, documents)
        if not raw_matches:
            if ARTICLE_MARKER.search(text):
                return self._query(
                    question,
                    language,
                    scope,
                    (),
                    raw_parser_matches=0,
                    validation_status="INVALID_REFERENCE_QUERY",
                    parser_confidence=0.0,
                    parser_failure_reason="article_marker_without_valid_number",
                )
            return None

        article_matches = self._deduplicate_matches(raw_matches)
        if self._has_ambiguous_numeric_continuation(text, article_matches):
            references, _mapping_ambiguous = self._associate_documents(article_matches, documents)
            references = self._deduplicate_references(references)
            return self._query(
                question,
                language,
                scope,
                references,
                raw_parser_matches=len(raw_matches),
                validation_status="INVALID_REFERENCE_QUERY",
                parser_confidence=0.0,
                parser_failure_reason="unmarked_adjacent_article_number",
            )
        references, mapping_ambiguous = self._associate_documents(article_matches, documents)
        references = self._deduplicate_references(references)
        if mapping_ambiguous:
            status = "AMBIGUOUS_REFERENCE_MAPPING"
            confidence = 0.0
            failure_reason = "article_document_association_is_ambiguous"
        else:
            status = "VALID"
            confidence = 1.0
            failure_reason = None
        return self._query(
            question,
            language,
            scope,
            references,
            raw_parser_matches=len(raw_matches),
            validation_status=status,
            parser_confidence=confidence,
            parser_failure_reason=failure_reason,
        )

    def _article_matches(self, text: str):
        primary = [
            match
            for match in [*FORWARD.finditer(text), *REVERSED.finditer(text)]
            if normalize_article_number(match.group("ref")).normalized_reference not in REFERENCE_STOPWORDS
        ]
        raw_matches = [self._article_match(text, match, "legal_marker") for match in primary]
        for match in primary:
            normalized_primary = normalize_article_number(match.group("ref")).normalized_reference
            for token in NUMBER_TOKEN.finditer(text, match.start(), match.end()):
                normalized_token = normalize_article_number(token.group("ref")).normalized_reference
                if normalized_token == normalized_primary:
                    raw_matches.append(self._article_match(text, token, "number_token"))
        return raw_matches

    def _article_match(self, text: str, match, pattern: str):
        normalized = normalize_article_number(match.group("ref"))
        return _ArticleMatch(
            ExactLegalReference(
                raw_text=text[match.start():match.end()],
                raw_article_number=match.group("ref").strip(),
                normalized_article_number=normalized.normalized_reference,
                article_suffix=normalized.suffix,
                raw_document_reference=None,
                normalized_document_reference="",
                resolved_document_id=None,
                start_offset=match.start(),
                end_offset=match.end(),
            ),
            pattern,
        )

    def _shorthand_matches(self, text: str, documents: tuple[_DocumentMention, ...]):
        matches = []
        for token in NUMBER_TOKEN.finditer(text):
            if any(token.start() < document.end and document.start < token.end() for document in documents):
                continue
            distance = min(self._distance(token.start(), token.end(), document.start, document.end) for document in documents)
            if distance <= 80:
                matches.append(self._article_match(text, token, "document_number_shorthand"))
        return matches

    def _deduplicate_matches(self, matches: list[_ArticleMatch]):
        selected = []
        for candidate in sorted(matches, key=lambda item: (item.reference.start_offset, -(item.reference.end_offset - item.reference.start_offset))):
            duplicate_index = next(
                (
                    index
                    for index, existing in enumerate(selected)
                    if candidate.reference.normalized_article_number == existing.reference.normalized_article_number
                    and self._overlaps(candidate.reference, existing.reference)
                ),
                None,
            )
            if duplicate_index is None:
                selected.append(candidate)
                continue
            existing = selected[duplicate_index]
            if self._span_length(candidate.reference) > self._span_length(existing.reference):
                selected[duplicate_index] = candidate
        return selected

    def _document_mentions(self, text: str, known_references: tuple[str, ...]):
        known = []
        for reference in sorted(set(known_references), key=len, reverse=True):
            if not reference:
                continue
            for match in re.finditer(re.escape(normalize_unicode(reference)), text, flags=re.I):
                known.append(
                    _DocumentMention(
                        raw=text[match.start():match.end()],
                        normalized=normalize_document_reference(reference),
                        start=match.start(),
                        end=match.end(),
                    )
                )
        if known:
            return self._deduplicate_documents(known)

        hinted = []
        for match in DOCUMENT_HINT.finditer(text):
            raw = match.group("document").strip()
            marker = ARTICLE_MARKER.search(raw)
            if marker:
                raw = raw[:marker.start()].strip(" :,-–—")
            normalized = normalize_document_reference(raw)
            if (
                raw
                and normalized
                and normalized not in {"l", "d", "de", "du", "des"}
                and self._plausible_document_reference(raw)
            ):
                start = match.start("document")
                hinted.append(_DocumentMention(raw, normalized, start, start + len(raw)))
        return self._deduplicate_documents(hinted)

    def _plausible_document_reference(self, value: str) -> bool:
        """Do not turn ordinary phrases such as 'de la facture' into code names."""
        return bool(
            re.search(
                r"\b(?:code|loi|d[ée]cret|arr[êe]t[ée]?|ordonnance|r[èe]glement|constitution|"
                r"convention|trait[ée]|journal officiel|jort)\b|"
                r"(?:مجلة|قانون|مرسوم|قرار|أمر|دستور)|"
                r"\b[A-Z][A-Z0-9_]{2,}\b",
                value,
                re.I if re.search(r"\b(?:code|loi|d[ée]cret|arr[êe]t[ée]?|ordonnance|r[èe]glement|constitution|convention|trait[ée]|journal officiel|jort)\b", value, re.I) else 0,
            )
        )

    def _deduplicate_documents(self, documents: list[_DocumentMention]):
        selected = []
        for candidate in sorted(documents, key=lambda item: (item.start, -(item.end - item.start))):
            if any(candidate.normalized == existing.normalized and candidate.start < existing.end and existing.start < candidate.end for existing in selected):
                continue
            selected.append(candidate)
        return tuple(selected)

    def _associate_documents(self, matches: list[_ArticleMatch], documents: tuple[_DocumentMention, ...]):
        if not documents:
            return tuple(match.reference for match in matches), False
        if len(documents) == 1:
            document = documents[0]
            return tuple(self._with_document(match.reference, document) for match in matches), False

        references = []
        ambiguous = False
        for match in matches:
            distances = [
                (self._distance(match.reference.start_offset, match.reference.end_offset, document.start, document.end), document)
                for document in documents
            ]
            minimum = min(distance for distance, _document in distances)
            nearest = [document for distance, document in distances if distance == minimum]
            if len({document.normalized for document in nearest}) != 1:
                ambiguous = True
                references.append(match.reference)
            else:
                references.append(self._with_document(match.reference, nearest[0]))
        return tuple(references), ambiguous

    def _with_document(self, reference: ExactLegalReference, document: _DocumentMention):
        return replace(
            reference,
            raw_document_reference=document.raw,
            normalized_document_reference=document.normalized,
        )

    def _deduplicate_references(self, references: tuple[ExactLegalReference, ...]):
        unique = {}
        for reference in references:
            key = (reference.normalized_article_number, reference.normalized_document_reference)
            unique.setdefault(key, reference)
        return tuple(unique.values())

    def _query(
        self,
        question,
        language,
        scope,
        references,
        *,
        raw_parser_matches,
        validation_status,
        parser_confidence,
        parser_failure_reason,
    ):
        first = references[0] if references else None
        query_type = "MULTI_EXACT_REFERENCE_QUERY" if len(references) > 1 else "EXACT_REFERENCE_QUERY"
        return ExactReferenceQuery(
            query_type=query_type,
            references=tuple(references),
            raw_parser_matches=raw_parser_matches,
            deduplicated_reference_count=len(references),
            source_type="legal_article",
            raw_document_reference=first.raw_document_reference if first else None,
            raw_reference=first.raw_article_number if first else "",
            article_number=first.normalized_article_number if first else "",
            normalized_reference=first.normalized_article_number if first else "",
            normalized_article_number=first.normalized_article_number if first else "",
            article_suffix=first.article_suffix if first else None,
            requested_document=first.raw_document_reference if first else None,
            original_question=question,
            language=language,
            explicit_scope=scope,
            validation_status=validation_status,
            parser_confidence=parser_confidence,
            detected_references=tuple(reference.normalized_article_number for reference in references),
            parser_failure_reason=parser_failure_reason,
        )

    def _overlaps(self, left: ExactLegalReference, right: ExactLegalReference):
        return left.start_offset < right.end_offset and right.start_offset < left.end_offset

    def _span_length(self, reference: ExactLegalReference):
        return reference.end_offset - reference.start_offset

    def _distance(self, left_start, left_end, right_start, right_end):
        if left_start < right_end and right_start < left_end:
            return 0
        if left_end <= right_start:
            return right_start - left_end
        return left_start - right_end

    def _has_ambiguous_numeric_continuation(self, text: str, matches: list[_ArticleMatch]):
        for match in matches:
            tail = text[match.reference.end_offset:]
            if re.match(r"^\s*[\d٠-٩۰-۹]+(?:\s|$)", tail):
                return True
        return False
