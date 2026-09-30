"""Verify source identity and verbatim evidence. This is not an entailment proof."""
import re
from app.utils.arabic import normalize_for_search

class CitationError(ValueError):
    pass

class CitationValidator:
    def validate(self, answer, sources):
        allowed = {str(source.id):source for source in sources}
        for claim in answer.claims:
            evidence = []
            for citation in claim.citations:
                source = allowed.get(citation.source_id)
                if source is None or citation.quote not in source.original_text:
                    raise CitationError("Référence inconnue ou citation non conforme au texte original")
                evidence.append(citation.quote + " " + (source.article_number or ""))
            available_numbers = set(re.findall(r"\d+", normalize_for_search(" ".join(evidence))))
            stated_numbers = set(re.findall(r"\d+", normalize_for_search(claim.text)))
            if not stated_numbers.issubset(available_numbers):
                raise CitationError("Un nombre ou une référence n'apparaît pas dans les sources citées")
            numbers = re.findall(r"(?:article|الفصل)\s*(\d+)", normalize_for_search(claim.text))
            legal_numbers = {normalize_for_search(s.article_number or "").split(" ")[0] for s in [allowed[q.source_id] for q in claim.citations] if s.source_type == "law"}
            if any(number not in legal_numbers for number in numbers):
                raise CitationError("Article juridique absent des sources récupérées")
            # A number present elsewhere in an article is not support for a new amount or duration.
            units=r"(?:%|pour\s+cent|jours?|mois|ans?|annees?|heures?|dinars?|tnd|euros?|يوم|يوما|ايام|شهر|اشهر|سنة|سنوات|دينار|دنانير|بالمائة)"
            quantities=re.findall(r"(?<!\w)\d+(?:[ .,]\d+)*\s*"+units,normalize_for_search(claim.text))
            quoted=normalize_for_search(" ".join(q.quote for q in claim.citations))
            for quantity in quantities:
                if quantity not in quoted:
                    raise CitationError("Montant, pourcentage ou durée non soutenu avec son unité par la citation.")
            for date in re.findall(r"\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b",normalize_for_search(claim.text)):
                if date not in quoted: raise CitationError("Date non soutenue par la citation.")
        return True
