from dataclasses import dataclass
import re
import unicodedata

from app.utils.arabic import normalize_for_search


ARTICLE_WORDS = r"(?:article|art\.?|الفصل|فصل)"
SUFFIX_PATTERN = r"(?:bis|ter|quater|مكرر(?:\s+\d+)?)"


@dataclass(frozen=True)
class NormalizedArticleReference:
    raw_reference: str
    normalized_reference: str
    suffix: str | None = None


def normalize_unicode(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    return text.replace("\u00a0", " ").replace("\u202f", " ")


def normalize_article_number(value: str) -> NormalizedArticleReference:
    raw = value.strip()
    text = normalize_unicode(raw)
    text = normalize_for_search(text)
    text = re.sub(rf"^\s*{ARTICLE_WORDS}\s+", "", text, flags=re.I)
    text = re.sub(rf"\s+{ARTICLE_WORDS}\s*$", "", text, flags=re.I)
    text = re.sub(r"[’'`]", " ", text)
    text = re.sub(r"[.:,;،؛()\[\]{}\-–—]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text in {"premier", "1er", "اول", "الاول", "االول", "الأول"}:
        text = "1"
    if text in {"unique", "الوحيد"}:
        text = "unique"
    suffix_match = re.search(rf"\b({SUFFIX_PATTERN})$", text, flags=re.I)
    suffix = suffix_match.group(1).strip().lower() if suffix_match else None
    return NormalizedArticleReference(raw_reference=raw, normalized_reference=text.lower(), suffix=suffix)


def normalize_article_reference(value: str) -> NormalizedArticleReference:
    return normalize_article_number(value)


def normalize_legal_reference(value: str) -> NormalizedArticleReference:
    return normalize_article_number(value)


def normalize_document_reference(value: str | None) -> str:
    if not value:
        return ""
    text = normalize_unicode(value)
    text = normalize_for_search(text)
    text = re.sub(r"\.[a-z0-9]{1,6}$", "", text, flags=re.I)
    text = re.sub(r"[_\-–—/\\]+", " ", text)
    text = re.sub(r"[^\w\u0600-\u06FF]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip().lower()


def normalize_document_label(value: str | None) -> str:
    return normalize_document_reference(value)
