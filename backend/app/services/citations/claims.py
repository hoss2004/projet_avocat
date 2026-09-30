"""Independent claim/evidence assessment with fail-closed generation control."""
import re

from app.schemas.assistant import GeneratedAnswer
from app.services.citations.validator import CitationError, CitationValidator
from app.services.legal_assistant.models import ClaimSupport, ClaimSupportStatus
from app.utils.arabic import normalize_for_search


class ClaimSourceValidator:
    """Assess every generated claim, then reject claims that are not fully supported."""

    def assess(self, answer, sources, provider) -> list[ClaimSupport]:
        allowed = {str(source.id): source for source in sources}
        results: dict[int, ClaimSupport] = {}
        pending = []

        for index, claim in enumerate(answer.claims):
            source_ids = list(dict.fromkeys(citation.source_id for citation in claim.citations))
            grounding_type = getattr(claim, "grounding_type", "SOURCE_BACKED")
            if grounding_type != "SOURCE_BACKED" and not claim.citations:
                if grounding_type == "GENERAL_REASONING" and self._looks_like_precise_positive_law(claim.text):
                    results[index] = ClaimSupport(
                        claim_index=index,
                        support_status=ClaimSupportStatus.UNSUPPORTED,
                        source_ids=[],
                        reason="Un raisonnement général ne peut pas contenir une référence positive précise non sourcée.",
                    )
                else:
                    labels = {
                        "GENERAL_REASONING":"Raisonnement général explicitement non présenté comme droit positif vérifié.",
                        "USER_PROVIDED_FACT":"Fait qualifié comme communiqué par l'utilisateur et non documenté.",
                        "MISSING_INFORMATION":"Information ou preuve manquante explicitement signalée.",
                    }
                    results[index] = ClaimSupport(
                        claim_index=index,
                        support_status=ClaimSupportStatus.NO_SOURCE,
                        source_ids=[],
                        reason=labels.get(grounding_type, "Énoncé non sourcé explicitement qualifié."),
                    )
                continue
            if not claim.citations or any(source_id not in allowed for source_id in source_ids):
                results[index] = ClaimSupport(
                    claim_index=index,
                    support_status=ClaimSupportStatus.NO_SOURCE,
                    source_ids=source_ids,
                    reason="Une citation ne correspond à aucune preuve récupérée.",
                )
                continue
            if any(citation.quote not in allowed[citation.source_id].original_text for citation in claim.citations):
                results[index] = ClaimSupport(
                    claim_index=index,
                    support_status=ClaimSupportStatus.UNSUPPORTED,
                    source_ids=source_ids,
                    reason="Une citation n'est pas une copie du texte original.",
                )
                continue
            try:
                CitationValidator().validate(GeneratedAnswer(claims=[claim]), sources)
            except CitationError as exc:
                results[index] = ClaimSupport(
                    claim_index=index,
                    support_status=ClaimSupportStatus.UNSUPPORTED,
                    source_ids=source_ids,
                    reason=str(exc),
                )
                continue
            if any(claim.text.strip() == citation.quote.strip() for citation in claim.citations):
                results[index] = ClaimSupport(
                    claim_index=index,
                    support_status=ClaimSupportStatus.SUPPORTED,
                    source_ids=source_ids,
                    reason="L'affirmation reproduit textuellement la preuve citée.",
                )
                continue
            substantive = [
                citation.quote
                for citation in claim.citations
                if len(re.sub(r"[\W\d_]", "", citation.quote)) >= 20
                and not re.fullmatch(
                    r"(?:article|art\.|الفصل)\s*\d+|\d+\s+الفصل",
                    normalize_for_search(citation.quote),
                )
            ]
            if not substantive:
                results[index] = ClaimSupport(
                    claim_index=index,
                    support_status=ClaimSupportStatus.UNSUPPORTED,
                    source_ids=source_ids,
                    reason="Un numéro d'article ou un intitulé court ne soutient pas une conclusion.",
                )
                continue
            pending.append((index, claim, source_ids))

        if pending and not hasattr(provider, "verify_claims"):
            for index, _claim, source_ids in pending:
                results[index] = ClaimSupport(
                    claim_index=index,
                    support_status=ClaimSupportStatus.UNSUPPORTED,
                    source_ids=source_ids,
                    reason="Le contrôle sémantique indépendant est indisponible.",
                )
        elif pending:
            payload = [
                {
                    "claim_index": index,
                    "claim": claim.text,
                    "evidence": [
                        {
                            "source_id": citation.source_id,
                            "quote": citation.quote,
                            "article_number": allowed[citation.source_id].article_number,
                            "source_type": allowed[citation.source_id].source_type,
                            "metadata": allowed[citation.source_id].metadata,
                        }
                        for citation in claim.citations
                    ],
                }
                for index, claim, _source_ids in pending
            ]
            verdicts = provider.verify_claims(payload)
            expected = {index for index, _claim, _source_ids in pending}
            verdict_by_index = {
                item.get("claim_index"):item for item in verdicts
                if isinstance(item, dict) and item.get("claim_index") in expected
            } if isinstance(verdicts, list) else {}
            for index, _claim, source_ids in pending:
                verdict = verdict_by_index.get(index)
                if verdict is None:
                    results[index] = ClaimSupport(
                        claim_index=index,
                        support_status=ClaimSupportStatus.UNSUPPORTED,
                        source_ids=source_ids,
                        reason="Le vérificateur n'a pas rendu de décision complète pour cette affirmation.",
                    )
                    continue
                status = self._status(verdict.get("verdict"), verdict.get("reason", ""))
                results[index] = ClaimSupport(
                    claim_index=index,
                    support_status=status,
                    source_ids=source_ids,
                    reason=str(verdict.get("reason") or "Aucune justification fournie."),
                )
        return [results[index] for index in range(len(answer.claims))]

    def _looks_like_precise_positive_law(self, text):
        normalized = normalize_for_search(text)
        return bool(re.search(
            r"(?:\b(?:article|art\.)\s*\d+|الفصل\s*\d+|\b(?:loi|décret|decret)\s+n[°ºo]?\s*\d+|"
            r"\b\d+(?:[.,]\d+)?\s*(?:%|jours?|mois|ans?|dinars?|tnd)\b|"
            r"\b(?:sanctions? p[ée]nales?|peine d['’]emprisonnement|est passible|peut [êe]tre condamn[ée]|"
            r"entra[iî]ne une? (?:responsabilit[ée]|nullit[ée]|sanction)|engage la responsabilit[ée]|"
            r"peut [êe]tre tenu(?:e)? responsable|ouvre droit [àa]|est tenu(?:e)? de)\b)",
            normalized,
            re.I,
        ))

    def validate(self, answer, sources, provider):
        assessments = self.assess(answer, sources, provider)
        rejected = [item for item in assessments if item.support_status != ClaimSupportStatus.SUPPORTED]
        if rejected:
            summary = ", ".join(f"claim {item.claim_index}: {item.support_status.value}" for item in rejected)
            raise CitationError(f"Affirmation insuffisamment soutenue ({summary}).")
        method = "verbatim" if not hasattr(provider, "verify_claims") else "independent_model_review"
        return {
            "method": method,
            "claims": len(assessments),
            "verdicts": [item.model_dump(mode="json") for item in assessments],
            "guarantee": False,
        }

    def _status(self, verdict, reason):
        value = str(verdict or "").casefold()
        reason_value = str(reason or "").casefold()
        uncertainty = (
            "cependant", "ne mentionne pas", "ne mentionnent pas", "ne soutient pas",
            "ne couvrent pas", "insuffisant", "pas explicitement", "nécessaire de vérifier",
            "not supported", "not explicitly", "insufficient", "however",
            "غير مدعوم", "غير كاف", "لا تدعم", "لا يدعم", "لا يذكر",
        )
        if any(marker in reason_value for marker in uncertainty):
            return ClaimSupportStatus.UNSUPPORTED
        if value == "supported":
            return ClaimSupportStatus.SUPPORTED
        if value in {"partially_supported", "partial"} or "partiel" in reason_value or "partially" in reason_value:
            return ClaimSupportStatus.PARTIALLY_SUPPORTED
        if value == "contradicted":
            return ClaimSupportStatus.CONTRADICTED
        if value in {"no_source", "missing_source"}:
            return ClaimSupportStatus.NO_SOURCE
        return ClaimSupportStatus.UNSUPPORTED
