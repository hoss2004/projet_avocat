import re

from app.services.legal_assistant.models import LegalIssue, LegalUserRequest


class LegalIssueAnalyzer:
    """Produce bounded, independent retrieval questions without adding facts."""

    def analyze(self, request: LegalUserRequest, planned_queries: list[str] | None = None) -> list[LegalIssue]:
        text = request.original_question.strip()
        if planned_queries:
            parts = [re.sub(r"\s+", " ", part).strip(" .,:؛،") for part in planned_queries]
            parts = [part for part in parts if self._meaningful(part)]
        else:
            parts = re.split(r"(?:[?؟]\s*|\n+|\s*;\s*|(?:^|\s)\d+[.)]\s+)", text)
            parts = [re.sub(r"\s+", " ", part).strip(" .,:؛،") for part in parts]
            parts = [part for part in parts if len(part) >= 8 and self._meaningful(part)]
        if not parts:
            parts = [text]
        # Avoid turning stylistic output instructions into standalone legal issues.
        filtered = [part for part in parts if not re.fullmatch(r"(?:r[ée]ponds?|r[ée]dige[rz]?|format)\b.*", part, re.I)]
        parts = (filtered or parts)[:8]
        issues = []
        for index, part in enumerate(parts, 1):
            title = part[:100] + ("…" if len(part) > 100 else "")
            search_query = self._compact_search_query(part)
            issues.append(
                LegalIssue(
                    id=f"ISSUE-{index}",
                    title=title,
                    description=part,
                    legal_questions=[part],
                    search_queries=[search_query],
                    explicit_references=request.explicit_references,
                )
            )
        return issues

    def _meaningful(self, value: str) -> bool:
        return bool(re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ\u0600-\u06ff]", value or ""))

    def _compact_search_query(self, value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        value = re.sub(
            r"^(?:r[ée]dige(?:z)?|pr[ée]pare(?:z)?|donne(?:z)?|fais|faites|produis(?:ez)?)\b.{0,120}?\b(?:sur|concernant|relative? [àa])\s+",
            "", value, flags=re.I,
        )
        sentences = re.split(r"(?<=[.!?])\s+", value)
        substantive = [
            sentence for sentence in sentences
            if not re.match(
                r"^(?:analyse|explique|pr[ée]sente|d[ée]veloppe|formule|conclus|cite)\b",
                sentence.strip(), re.I,
            )
        ]
        compact = " ".join(substantive or sentences).strip(" .")
        return compact[:1200] or value[:1200]
