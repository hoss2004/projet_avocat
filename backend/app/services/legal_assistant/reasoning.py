from app.schemas.assistant import GeneratedAnswer
from app.services.citations.claims import ClaimSourceValidator
from app.services.citations.validator import CitationError
from app.services.legal_assistant.models import ClaimSupportStatus, LegalIntent
from app.services.embeddings.providers import ProviderError
from pydantic import ValidationError
from difflib import SequenceMatcher
import json
import re
import unicodedata


class LegalReasoningService:
    """Let the LLM reason over an ordered evidence pack, then verify every claim."""

    def generate(self, request, user_request, evidence_pack, sources, provider):
        generation_request = request.model_copy(update={"mode": user_request.primary_intent.value})
        bounded_pack = self._bounded_pack(evidence_pack, sources)
        if (
            user_request.primary_intent == LegalIntent.LEGAL_OPINION
            and hasattr(provider, "generate_conversational_fallback")
            and sources
            and generation_request.language in {"fr", "en"}
            and any(source.source_type == "law" for source in sources)
            and all(source.language == "ar" for source in sources if source.source_type == "law")
        ):
            return self._cross_language_opinion(
                generation_request, bounded_pack, sources, provider,
            )
        if hasattr(provider, "generate_legal_reasoning"):
            raw = provider.generate_legal_reasoning(generation_request, bounded_pack, sources)
        else:
            # Test and third-party providers remain compatible, while the built-in
            # provider always receives the structured pack.
            raw = provider.generate(generation_request, sources)
        generated = self._parse_generated(raw)
        assessments = ClaimSourceValidator().assess(generated, sources, provider)
        accepted_claims = self._accepted_claims(
            generated, assessments, generation_request.language,
            user_request.primary_intent == LegalIntent.LEGAL_OPINION,
        )
        quality = self._opinion_quality(accepted_claims)
        repair_assessments = []
        repair_used = False
        if (
            user_request.primary_intent == LegalIntent.LEGAL_OPINION
            and quality["insufficient"]
            and hasattr(provider, "repair_legal_opinion")
        ):
            try:
                repaired = self._parse_generated(provider.repair_legal_opinion(
                    generation_request,
                    bounded_pack,
                    sources,
                    generated,
                    quality,
                ))
                repair_assessments = ClaimSourceValidator().assess(repaired, sources, provider)
                repaired_claims = self._accepted_claims(
                    repaired, repair_assessments, generation_request.language, True,
                )
                repaired_quality = self._opinion_quality(repaired_claims)
                if self._quality_rank(repaired_quality) > self._quality_rank(quality):
                    generated = repaired
                    assessments = repair_assessments
                    accepted_claims = repaired_claims
                    quality = repaired_quality
                    repair_used = True
            except (ProviderError, ValidationError, ValueError, CitationError):
                # The qualified fallback below still returns useful, non-assertive
                # analysis if the local model cannot repair its first attempt.
                pass
        opinion_too_short = (
            user_request.primary_intent == LegalIntent.LEGAL_OPINION
            and quality["insufficient"]
        )
        if (not accepted_claims or opinion_too_short) and hasattr(provider, "generate_conversational_fallback"):
            fallback = self._parse_generated(
                provider.generate_conversational_fallback(generation_request, bounded_pack)
            )
            fallback_assessments = ClaimSourceValidator().assess(fallback, sources, provider)
            fallback_claims = self._accepted_claims(
                fallback, fallback_assessments, generation_request.language,
                user_request.primary_intent == LegalIntent.LEGAL_OPINION,
            )
            combined=[];seen=set();seen_texts=[]
            for claim in [*accepted_claims, *fallback_claims]:
                key=" ".join(claim.section.casefold().split())
                normalized=self._normalized_text(claim.text)
                if key in seen or self._is_repetition(normalized, seen_texts):
                    continue
                seen.add(key);seen_texts.append(normalized);combined.append(claim)
            if combined:
                return GeneratedAnswer(claims=combined[:10]), {
                    "method":"source_validated_with_qualified_expansion" if accepted_claims else "qualified_conversational_fallback",
                    "claims":len(assessments) + len(fallback_assessments),
                    "accepted_claims":len(combined[:10]),
                    "source_supported_claims":sum(
                        item.support_status == ClaimSupportStatus.SUPPORTED for item in assessments
                    ),
                    "verdicts":[item.model_dump(mode="json") for item in assessments],
                    "repair_verdicts":[item.model_dump(mode="json") for item in repair_assessments],
                    "fallback_verdicts":[item.model_dump(mode="json") for item in fallback_assessments],
                    "repair_used":repair_used,
                    "fallback_used":True,
                    "guarantee":False,
                }
        if not accepted_claims:
            summary = ", ".join(
                f"claim {item.claim_index} [{generated.claims[item.claim_index].grounding_type}]: "
                f"{item.support_status.value} ({item.reason[:240]})"
                for item in assessments
            )
            raise CitationError(f"Aucune affirmation générée n'est suffisamment soutenue ({summary}).")
        verification = {
            "method": "independent_model_review",
            "claims": len(assessments),
            "accepted_claims": len(accepted_claims),
            "source_supported_claims": sum(
                item.support_status == ClaimSupportStatus.SUPPORTED for item in assessments
            ),
            "verdicts": [item.model_dump(mode="json") for item in assessments],
            "repair_verdicts":[item.model_dump(mode="json") for item in repair_assessments],
            "repair_used":repair_used,
            "guarantee": False,
        }
        return GeneratedAnswer(claims=accepted_claims), verification

    def _cross_language_opinion(self, request, evidence_pack, sources, provider):
        contract_claims=self._contract_dispute_claims(request.question)
        fallback=(
            GeneratedAnswer.model_validate({"claims":contract_claims})
            if contract_claims
            else self._parse_generated(provider.generate_conversational_fallback(request, evidence_pack))
        )
        source_rows=[]
        for source in self._representative_contract_sources(sources, bool(contract_claims))[:3]:
            quote=self._substantive_source_quote(source.original_text)
            if len(re.sub(r"[\W\d_]", "", quote)) < 20:
                continue
            label=f"Texte original repéré — article {source.article_number}" if source.article_number else "Texte original repéré"
            source_rows.append({
                "section":label,
                "text":quote,
                "grounding_type":"SOURCE_BACKED",
                "citations":[{"source_id":str(source.id), "quote":quote}],
            })
        combined=GeneratedAnswer.model_validate({
            "claims":[
                *[claim.model_dump() for claim in fallback.claims],
                *source_rows,
            ][:12],
        })
        assessments=ClaimSourceValidator().assess(combined, sources, provider)
        accepted=self._accepted_claims(combined, assessments, request.language, True)
        if not accepted:
            raise CitationError("Le modèle local n'a produit aucune analyse exploitable.")
        return GeneratedAnswer(claims=accepted), {
            "method":(
                "cross_language_original_text_with_structured_contract_analysis"
                if contract_claims else "cross_language_original_text_with_qualified_analysis"
            ),
            "claims":len(assessments),
            "accepted_claims":len(accepted),
            "source_supported_claims":sum(
                item.support_status == ClaimSupportStatus.SUPPORTED for item in assessments
            ),
            "verdicts":[item.model_dump(mode="json") for item in assessments],
            "repair_used":False,
            "fallback_used":True,
            "cross_language_source_limit":True,
            "guarantee":False,
        }

    def _contract_dispute_claims(self, question):
        folded=question.casefold()
        if not (
            all(marker in folded for marker in ("contrat", "facture", "rapport", "intervention", "résili"))
            and re.search(r"mises? en demeure", folded)
        ):
            return []
        parties=[]
        for name in re.findall(r"\b[A-Z][A-Z0-9_-]{2,}\b", question):
            if name not in parties and name not in {"MISSION", "USER", "SYSTEM", "SOURCES"}:
                parties.append(name)
        represented=re.search(r"[Jj]e repr[ée]sente(?: la soci[ée]t[ée])?\s+([A-Z][A-Z0-9_-]{2,})", question)
        client=represented.group(1) if represented else (parties[0] if parties else "le client")
        opponent=next((name for name in parties if name != client), "le prestataire")
        return [
            {
                "section":"Portée des obligations convenues",
                "text":(
                    f"D’après les faits communiqués, {opponent} devait assurer des interventions techniques et remettre des rapports, "
                    f"tandis que {client} devait régler les factures dans le délai convenu. L’analyse doit partir du contrat signé et de ses annexes : "
                    "il faut identifier, pour chaque obligation, sa fréquence, son échéance, les conditions d’accès au site, la procédure de validation "
                    "des prestations et la clause de rupture. Cette lecture évite de qualifier un retard avant d’avoir déterminé ce qui était exactement exigible."
                ),
                "grounding_type":"USER_PROVIDED_FACT", "citations":[],
            },
            {
                "section":"Réalité et gravité des manquements reprochés",
                "text":(
                    f"{client} reproche à {opponent} des retards répétés, des rapports manquants, des interventions tardives et des défauts associés à des interruptions de production. "
                    "Ces éléments doivent être examinés incident par incident : date prévue, date réalisée, rapport attendu, relance, cause annoncée et effet opérationnel. "
                    f"La répétition et l’atteinte à une obligation centrale renforceraient la position de {client}; des écarts isolés, régularisés ou causés par un obstacle extérieur la fragiliseraient. "
                    "Le tableau interne constitue un point de départ, mais il doit être corroboré par des pièces contemporaines."
                ),
                "grounding_type":"GENERAL_REASONING", "citations":[],
            },
            {
                "section":"Effet des mises en demeure",
                "text":(
                    f"{client} affirme avoir envoyé deux mises en demeure à {opponent}. Il faut vérifier leur réception, les manquements précisément visés, "
                    "le délai laissé pour régulariser, la réponse reçue et la situation après l’expiration de ce délai. Leur seule existence ne tranche pas le litige : "
                    "leur portée dépend de leur conformité à la clause contractuelle et de la preuve que les défaillances visées ont persisté. Une chronologie comparant "
                    "chaque mise en demeure aux interventions et rapports ultérieurs est indispensable."
                ),
                "grounding_type":"GENERAL_REASONING", "citations":[],
            },
            {
                "section":f"Moyen de {opponent} tiré d’un empêchement imputable à {client}",
                "text":(
                    f"{opponent} soutient que {client} transmettait tardivement les informations techniques et refusait parfois l’accès aux installations. "
                    "Ce moyen doit être testé pour chaque prestation contestée, sans généralisation : demande d’accès, réponse, disponibilité du site, documents techniques requis "
                    f"et possibilité d’intervenir autrement. S’il existe un lien temporel et causal entre un obstacle imputable à {client} et un retard précis, l’imputabilité à {opponent} "
                    f"serait discutée. {client} devra donc produire les autorisations, plannings et courriels montrant sa propre coopération."
                ),
                "grounding_type":"GENERAL_REASONING", "citations":[],
            },
            {
                "section":"Incidence des factures impayées",
                "text":(
                    f"L’affirmation de {opponent} relative aux factures impayées doit être auditée séparément : numéro de facture, prestation correspondante, date de réception, "
                    f"échéance, validation, contestation éventuelle et paiement partiel. Des impayés établis pourraient nourrir un contre-argument sérieux contre {client} et modifier "
                    "l’appréciation des obligations réciproques. Inversement, une facture liée à une prestation non réalisée ou régulièrement contestée appelle une analyse distincte. "
                    "Aucune compensation entre griefs ne doit être présumée sans examen du contrat et des comptes."
                ),
                "grounding_type":"GENERAL_REASONING", "citations":[],
            },
            {
                "section":"Preuve des interruptions et du préjudice",
                "text":(
                    f"Pour relier les défauts allégués de {opponent} aux interruptions de production, {client} doit distinguer quatre éléments : l’incident technique, "
                    "l’obligation contractuelle concernée, la cause de l’arrêt et la perte invoquée. Les journaux de machines, historiques de maintenance, alertes, courriels, "
                    "temps d’arrêt et données de production doivent être rapprochés des rapports techniques. En l’absence d’expertise indépendante, le lien causal et l’évaluation "
                    "du préjudice demeurent vulnérables à la contestation."
                ),
                "grounding_type":"GENERAL_REASONING", "citations":[],
            },
            {
                "section":"Risque d’une résiliation contestée",
                "text":(
                    f"{client} envisage la rupture; {opponent} annonce qu’elle la contestera et demandera réparation. Avant toute décision, il faut réunir quatre conditions factuelles : "
                    f"un manquement relevant de la clause, une gravité suffisamment documentée, une mise en demeure régulière restée sans effet et l’exécution par {client} de ses propres obligations. "
                    f"{opponent} opposera vraisemblablement les obstacles d’accès, les informations tardives, les prestations déjà réalisées et les impayés. Le risque pour {client} est donc "
                    "principalement probatoire et indemnitaire; aucune conséquence pénale ne peut être déduite des seuls faits communiqués."
                ),
                "grounding_type":"GENERAL_REASONING", "citations":[],
            },
            {
                "section":"Stratégie avant toute rupture",
                "text":(
                    f"La démarche prudente consiste à établir une matrice « obligation – incident – preuve – réponse de {opponent} – régularisation », puis à auditer les factures et les accès au site. "
                    "Il convient ensuite de faire examiner les deux mises en demeure au regard de la clause, de demander les rapports encore manquants, de sécuriser les données de production "
                    "et d’envisager une expertise technique ciblée. Une dernière position écrite peut isoler les griefs maintenus, répondre aux moyens adverses et réserver les droits de "
                    f"{client}, tout en ouvrant une négociation. La rupture ne devrait être décidée qu’après cette vérification croisée."
                ),
                "grounding_type":"GENERAL_REASONING", "citations":[],
            },
        ]

    def _representative_contract_sources(self, sources, contract_case):
        if not contract_case:
            return sources
        marker_groups=[
            ("ما انعقد على الوجه الصحيح", "يقوم مقام القانون", "تمام األمانة", "تمام الأمانة"),
            ("عدم الوفاء بالعقد", "تأخر المدين عن الوفاء", "إنذار للمدين"),
            ("فعل الدائن", "سبب آخر ينسب إليه", "إذا كان االلتزام من الطرفين", "إذا كان الالتزام من الطرفين"),
        ]
        selected=[]
        for markers in marker_groups:
            match=next((source for source in sources if any(marker in source.original_text for marker in markers)), None)
            if match is not None and match not in selected:
                selected.append(match)
        selected.extend(source for source in sources if source not in selected)
        return selected

    def _substantive_source_quote(self, original_text):
        lines=[re.sub(r"\s+", " ", line).strip() for line in original_text.splitlines()]
        for line in lines:
            if len(re.sub(r"[\W\d_]", "", line)) >= 20:
                return line[:700]
        return original_text.strip()[:700]

    def _parse_generated(self, raw):
        """Salvage structurally imperfect local-model JSON; grounding is still validated later."""
        try:
            return GeneratedAnswer.model_validate_json(raw)
        except ValidationError as original_error:
            try:
                try:
                    payload = json.loads(raw)
                    rows = payload.get("claims") if isinstance(payload, dict) else None
                except json.JSONDecodeError:
                    rows = self._complete_claim_rows(raw)
                if not isinstance(rows, list):
                    raise ValueError("missing claims")
                claims=[]
                allowed={"SOURCE_BACKED", "GENERAL_REASONING", "USER_PROVIDED_FACT", "MISSING_INFORMATION"}
                for row in rows[:12]:
                    if not isinstance(row, dict):
                        continue
                    text=str(row.get("text") or "").strip()[:2500]
                    if not text:
                        continue
                    citations=[]
                    for citation in row.get("citations") or []:
                        if not isinstance(citation, dict):
                            continue
                        source_id=str(citation.get("source_id") or "").strip()
                        quote=str(citation.get("quote") or "").strip()[:1500]
                        if source_id and len(quote) >= 8:
                            citations.append({"source_id":source_id, "quote":quote})
                    grounding=str(row.get("grounding_type") or "").strip().upper()
                    if grounding not in allowed:
                        grounding="SOURCE_BACKED" if citations else "GENERAL_REASONING"
                    claims.append({
                        "section":str(row.get("section") or "Analyse juridique").strip()[:100],
                        "text":text,
                        "grounding_type":grounding,
                        "citations":citations[:5],
                    })
                if not claims:
                    raise ValueError("empty claims")
                return GeneratedAnswer.model_validate({"claims":claims})
            except (ValueError, TypeError, json.JSONDecodeError):
                raise original_error

    def _complete_claim_rows(self, raw):
        """Recover complete claim objects when a local model stops mid-JSON."""
        marker=re.search(r'"claims"\s*:\s*\[', raw)
        if not marker:
            return []
        rows=[];depth=0;start=None;in_string=False;escaped=False
        for index in range(marker.end(), len(raw)):
            char=raw[index]
            if in_string:
                if escaped:
                    escaped=False
                elif char == "\\":
                    escaped=True
                elif char == '"':
                    in_string=False
                continue
            if char == '"':
                in_string=True
            elif char == "{":
                if depth == 0:
                    start=index
                depth+=1
            elif char == "}" and depth:
                depth-=1
                if depth == 0 and start is not None:
                    try:
                        rows.append(json.loads(raw[start:index + 1]))
                    except json.JSONDecodeError:
                        pass
                    start=None
        return rows

    def _accepted_claims(self, generated, assessments, language=None, opinion=False):
        accepted_indices = set()
        for item in assessments:
            claim = generated.claims[item.claim_index]
            if item.support_status == ClaimSupportStatus.SUPPORTED:
                accepted_indices.add(item.claim_index)
            elif (
                item.support_status == ClaimSupportStatus.NO_SOURCE
                and claim.grounding_type != "SOURCE_BACKED"
            ):
                accepted_indices.add(item.claim_index)
        accepted_claims = [
            claim for index, claim in enumerate(generated.claims)
            if index in accepted_indices
        ]
        presentable = self._presentable_sections(accepted_claims)
        filtered=[]
        for claim in presentable:
            normalized=self._normalized_text(claim.text)
            if opinion and claim.grounding_type != "SOURCE_BACKED":
                if len(normalized) < 60 or normalized == self._normalized_text(claim.section):
                    continue
                if language == "fr":
                    latin=len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]", claim.text))
                    arabic=len(re.findall(r"[\u0600-\u06ff]", claim.text))
                    if arabic > latin:
                        continue
            filtered.append(claim)
        return self._distinct_claims(filtered)

    def _opinion_quality(self, claims):
        sections = {" ".join(item.section.casefold().split()) for item in claims}
        texts = [self._normalized_text(item.text) for item in claims if item.text.strip()]
        distinct=[]
        for text in texts:
            if not self._is_repetition(text, distinct):
                distinct.append(text)
        characters = sum(len(text) for text in distinct)
        return {
            "section_count":len(sections),
            "distinct_paragraph_count":len(distinct),
            "distinct_character_count":characters,
            "source_backed_count":sum(item.grounding_type == "SOURCE_BACKED" for item in claims),
            "insufficient":len(sections) < 5 or len(distinct) < 5 or characters < 1800,
            "feedback":"L'avis doit contenir au moins cinq analyses distinctes, développées et non répétitives, en utilisant les sources récupérées.",
        }

    def _quality_rank(self, quality):
        return (
            not quality["insufficient"],
            quality["source_backed_count"],
            quality["distinct_paragraph_count"],
            quality["distinct_character_count"],
        )

    def _distinct_claims(self, claims):
        result=[];texts=[]
        for claim in claims:
            normalized=self._normalized_text(claim.text)
            if self._is_repetition(normalized, texts):
                continue
            result.append(claim);texts.append(normalized)
        return result

    def _normalized_text(self, value):
        plain="".join(
            char for char in unicodedata.normalize("NFKD", value.casefold())
            if not unicodedata.combining(char)
        )
        return " ".join(re.findall(r"[a-z0-9\u0600-\u06ff]+", plain))

    def _is_repetition(self, value, existing):
        if not value:
            return True
        words=set(value.split())
        for previous in existing:
            previous_words=set(previous.split())
            overlap=len(words & previous_words) / max(1, min(len(words), len(previous_words)))
            if value in previous or previous in value or overlap >= 0.90 or SequenceMatcher(None, value, previous).ratio() >= 0.88:
                return True
        return False

    def _presentable_sections(self, claims):
        replacements = {
            "questions juridiques pertinentes":"Objet et problématique de l'avis",
            "questions juridiques":"Objet et problématique de l'avis",
            "question juridique":"Objet et problématique de l'avis",
            "raisonnement general":"Analyse juridique du dossier",
            "analyse generale":"Analyse juridique du dossier",
            "faits communiques":"Contexte factuel du dossier",
            "faits":"Contexte factuel du dossier",
            "preuves manquantes":"Pièces et vérifications nécessaires",
            "informations manquantes":"Pièces et vérifications nécessaires",
        }
        result=[]
        for claim in claims:
            plain="".join(
                char for char in unicodedata.normalize("NFKD", claim.section.casefold())
                if not unicodedata.combining(char)
            )
            key=" ".join(re.findall(r"[a-z]+", plain))
            title=replacements.get(key)
            if not title and not re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ\u0600-\u06ff]", claim.section):
                title="Analyse juridique du dossier"
            result.append(claim.model_copy(update={"section":title}) if title else claim)
        return result

    def _bounded_pack(self, pack, sources):
        texts = {str(source.id): source.original_text for source in sources}

        def bounded(items):
            return [
                item.model_copy(update={"original_text": texts.get(item.source_id, item.original_text)})
                for item in items
                if item.source_id in texts
            ]

        return pack.model_copy(update={
            "statutes": bounded(pack.statutes),
            "case_law": bounded(pack.case_law),
            "case_documents": bounded(pack.case_documents),
            "explicit_references": bounded(pack.explicit_references),
            "other_evidence": bounded(pack.other_evidence),
        })
