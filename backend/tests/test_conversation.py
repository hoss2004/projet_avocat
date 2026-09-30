import json
from uuid import uuid4

from app.schemas.assistant import Source
from app.services.legal_assistant.conversation import resolve_draft_section
from app.services.legal_assistant.evidence import EvidencePackBuilder
from app.services.legal_assistant.models import LegalIssue
from app.services.legal_assistant.orchestrator import LegalChatOrchestrator
from app.schemas.assistant import RagRequest
from test_ingestion_api import client


def _plan(primary, **updates):
    value = {
        "primary_goal": primary,
        "secondary_goals": [],
        "issue_queries": [],
        "requested_document_type": None,
        "requested_transformations": [],
        "mentioned_documents": [],
        "needs_legal_research": False,
        "needs_case_law": False,
        "needs_case_documents": False,
        "needs_active_draft": False,
        "needs_case_context": False,
        "draft_operation": None,
        "target_section": None,
        "new_case_facts": [],
        "response_strategy": "Test conversationnel contrôlé.",
    }
    value.update(updates)
    return json.dumps(value)


class MultiTurnModel:
    def plan_legal_chat(self, request, seed_request, prompt_context):
        question = request.question.casefold()
        if "avis complet" in question:
            return _plan(
                "LEGAL_OPINION",
                issue_queries=["validité de la position", "preuve de l'exécution"],
                needs_legal_research=True,
                needs_case_documents=True,
                needs_case_context=True,
            )
        if "développe" in question:
            return _plan("DRAFT_EDIT", needs_active_draft=True, draft_operation="EXPAND", target_section="Analyse")
        if "supprime" in question:
            return _plan("DRAFT_EDIT", needs_active_draft=True, draft_operation="DELETE", target_section="Risques")
        if "ajoute ce fait" in question:
            return _plan(
                "NEW_CASE_FACT", secondary_goals=["CASE_CHAT"], needs_case_context=True,
                new_case_facts=["Un virement complémentaire a été annoncé par le client."],
            )
        if "change ton analyse" in question:
            return _plan("CASE_CHAT", needs_case_context=True)
        if "mets à jour" in question:
            return _plan(
                "DRAFT_EDIT", needs_active_draft=True, needs_case_context=True,
                draft_operation="UPDATE_WITH_NEW_FACTS", target_section="Analyse",
            )
        if "courrier" in question:
            return _plan(
                "DRAFT_EDIT", needs_active_draft=True, draft_operation="REWRITE",
                requested_document_type="lettre_client",
            )
        return _plan("SIMPLE_CHAT")

    def generate_legal_reasoning(self, request, evidence_pack, sources):
        if "change ton analyse" in request.question.casefold():
            return json.dumps({"claims":[{
                "section":"Impact provisoire",
                "text":"Le fait nouveau peut influer sur l'analyse de l'exécution, sous réserve de vérifier le justificatif et sa chronologie.",
                "grounding_type":"GENERAL_REASONING", "citations":[],
            }]})
        return json.dumps({"claims":[
            {"section":"Faits", "text":"Les faits communiqués doivent être distingués des faits établis par les pièces.", "grounding_type":"USER_PROVIDED_FACT", "citations":[]},
            {"section":"Analyse", "text":"L'analyse confronte chaque condition aux preuves disponibles, aux objections adverses et aux explications alternatives.", "grounding_type":"GENERAL_REASONING", "citations":[]},
            {"section":"Risques", "text":"Une conclusion ferme suppose de vérifier les documents annoncés et la chronologie.", "grounding_type":"MISSING_INFORMATION", "citations":[]},
        ]})

    def generate_conversation(self, request, prompt_context):
        return json.dumps({
            "answer":"Le fait nouveau est mémorisé. Son justificatif et sa date devront être vérifiés avant de réévaluer définitivement l'avis.",
            "current_focus":"fait nouveau et incidence sur l'exécution",
        })

    def revise_legal_draft(self, request, user_task, prompt_context, sources):
        operation = user_task.draft_operation.value
        if operation == "EXPAND":
            result = {"target_section":"Analyse", "replacement_title":None, "replacement_content":"Analyse développée : condition, faits allégués, preuve, objection adverse, réponse et conclusion prudente.", "delete_target":False, "full_rewrite":None}
        elif operation == "DELETE":
            result = {"target_section":"Risques", "replacement_title":None, "replacement_content":None, "delete_target":True, "full_rewrite":None}
        elif operation == "UPDATE_WITH_NEW_FACTS":
            result = {"target_section":"Analyse", "replacement_title":None, "replacement_content":"Analyse mise à jour avec le virement annoncé, sous réserve de son justificatif et de sa date.", "delete_target":False, "full_rewrite":None}
        elif operation == "REWRITE":
            sections = [{"title":"Courrier au client", "content":"Madame, Monsieur, voici l'état prudent de notre analyse et les vérifications encore nécessaires."}]
            content = "\n\n".join(item["title"] + "\n" + item["content"] for item in sections)
            result = {"target_section":"Courrier au client", "replacement_title":None, "replacement_content":None, "delete_target":False, "full_rewrite":{
                "content":content, "sections":sections, "modified_section":"Courrier au client", "explanation":"Transformation complète demandée."
            }}
        result["explanation"] = "Modification ciblée effectuée."
        return json.dumps(result)


def test_seven_turn_conversation_persists_case_and_versions_draft(client, monkeypatch):
    api, settings = client
    settings.llm_provider = "ollama"
    monkeypatch.setattr("app.services.rag.engine.llm_provider", lambda settings: MultiTurnModel())
    case = api.post("/cases", json={"title":"Dossier conversationnel", "reference":"CHAT-1"}).json()

    def ask(question, conversation_id=None):
        response = api.post("/rag/query", json={
            "question":question, "case_id":case["id"], "scope":"LEGAL_AND_CASE",
            "mode":"LEGAL_OPINION", "conversation_id":conversation_id, "debug":True,
        })
        assert response.status_code == 200, response.text
        return response.json()

    turn1 = ask("Voici mon dossier. Rédige un avis complet.")
    conversation_id = turn1["conversation_id"]
    draft_id = turn1["draft_id"]
    assert turn1["draft_created"] is True
    assert turn1["draft_version"] == 1
    assert len(turn1["debug"]["tool_calls"]) >= 3

    turn2 = ask("Développe davantage le troisième argument.", conversation_id)
    assert turn2["draft_id"] == draft_id and turn2["draft_version"] == 2
    assert turn2["draft_operation"] == "EXPAND"

    turn3 = ask("Supprime le paragraphe sur les risques.", conversation_id)
    assert turn3["draft_version"] == 3
    assert all(item["title"] != "Risques" for item in api.get(f"/drafts/{draft_id}").json()["sections"])

    turn4 = ask("Ajoute ce fait : un virement complémentaire existe.", conversation_id)
    assert turn4["case_state_updated"] is True

    turn5 = ask("Est-ce que cela change ton analyse ?", conversation_id)
    assert turn5["draft_version"] == 3
    assert "justificatif" in turn5["answer"]

    turn6 = ask("Oui, mets à jour l'avis.", conversation_id)
    assert turn6["draft_version"] == 4

    turn7 = ask("Maintenant transforme-le en courrier adressé au client.", conversation_id)
    assert turn7["draft_version"] == 5
    assert "Courrier au client" in turn7["answer"]

    conversation = api.get(f"/conversations/{conversation_id}").json()
    assert len(conversation["turns"]) == 14
    assert conversation["current_draft_id"] == draft_id
    versions = api.get(f"/drafts/{draft_id}/versions").json()
    assert [item["version"] for item in versions] == [5, 4, 3, 2, 1]
    state = api.get(f"/cases/{case['id']}/state").json()
    assert any("virement complémentaire" in item["text"] for item in state["facts"])


def test_fallback_agent_skips_rag_for_greeting_and_resolves_draft_operation():
    router = LegalChatOrchestrator()
    greeting = router.understand(RagRequest(question="Bonjour"), None, None)
    assert greeting.user_task.primary_goal.value == "SIMPLE_CHAT"
    assert greeting.conversation_decision.intent.value == "GENERAL_CHAT"
    assert greeting.user_task.tool_calls == []
    context = {
        "active_draft":{"draft_id":"00000000-0000-0000-0000-000000000001", "sections":[
            {"title":"Faits", "content":"x"}, {"title":"Analyse", "content":"y"},
        ]},
        "last_modified_section":"Analyse",
    }
    edit = router.understand(RagRequest(question="Développe la deuxième partie"), None, None, prompt_context=context)
    assert edit.user_task.primary_goal.value == "DRAFT_EDIT"
    assert edit.user_task.draft_operation.value == "EXPAND"
    assert edit.user_task.target_section == "Analyse"
    assert [item.name.value for item in edit.user_task.tool_calls] == ["get_active_draft"]


def test_greeting_after_draft_never_loads_returns_or_modifies_draft(client, monkeypatch):
    api, settings = client
    settings.llm_provider = "ollama"

    class Model(MultiTurnModel):
        def plan_legal_chat(self, request, seed_request, prompt_context):
            if request.question.casefold() == "bonjour":
                # Deliberately bad model decision: the deterministic safety guard must override it.
                return _plan(
                    "DRAFT_EDIT", needs_active_draft=True,
                    draft_operation="REWRITE", target_section="Analyse",
                )
            return super().plan_legal_chat(request, seed_request, prompt_context)

        def generate_conversation(self, request, prompt_context):
            assert "active_draft" not in prompt_context
            assert request.question == "bonjour"
            return json.dumps({"answer":"Bonjour ! Comment puis-je vous aider ?", "current_focus":None})

        def revise_legal_draft(self, *args, **kwargs):
            raise AssertionError("A greeting must never load or revise the active draft")

    monkeypatch.setattr("app.services.rag.engine.llm_provider", lambda settings: Model())
    first = api.post("/rag/query", json={
        "question":"Voici mon dossier. Rédige un avis complet.",
        "mode":"LEGAL_OPINION", "debug":True,
    }).json()
    draft_id = first["draft_id"]
    greeting = api.post("/rag/query", json={
        "question":"bonjour", "mode":"LEGAL_OPINION",
        "conversation_id":first["conversation_id"], "debug":True,
    })
    assert greeting.status_code == 200, greeting.text
    result = greeting.json()
    assert result["intent"] == "GENERAL_CHAT"
    assert result["response_mode"] == "CHAT"
    assert result["answer"] == "Bonjour ! Comment puis-je vous aider ?"
    assert "Faits" not in result["answer"]
    assert result["legal_issues"] == [] and result["retrieved_sources"] == []
    assert result["draft_id"] == draft_id and result["draft_version"] == 1
    assert result["draft_modified"] is False
    assert result["draft_loaded_for_generation"] is False
    assert result["debug"]["retrieval_required"] is False
    assert result["debug"]["legal_issue_analyzer_called"] is False
    assert result["debug"]["draft_modified"] is False
    assert len(api.get(f"/drafts/{draft_id}/versions").json()) == 1


def test_duplicate_generated_sections_are_rendered_once(client, monkeypatch):
    api, settings = client
    settings.llm_provider = "ollama"

    class DuplicateModel(MultiTurnModel):
        def generate_legal_reasoning(self, request, evidence_pack, sources):
            return json.dumps({"claims":[
                    {"section":"Problématiques", "text":"La première formulation distingue les allégations, les faits contestés et les vérifications documentaires encore nécessaires.", "grounding_type":"GENERAL_REASONING", "citations":[]},
                    {"section":"Problématiques", "text":"La seconde formulation examine séparément les arguments adverses et les réponses possibles sans anticiper l'issue du dossier.", "grounding_type":"GENERAL_REASONING", "citations":[]},
                    {"section":"Conclusion", "text":"Une conclusion prudente dépendra de la chronologie complète, des pièces originales et des règles qui auront été vérifiées.", "grounding_type":"GENERAL_REASONING", "citations":[]},
            ]})

    monkeypatch.setattr("app.services.rag.engine.llm_provider", lambda settings: DuplicateModel())
    response = api.post("/rag/query", json={
        "question":"Voici mon dossier. Rédige un avis complet.",
        "mode":"LEGAL_OPINION", "debug":True,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["debug"]["generated_sections"] == ["Problématiques", "Problématiques", "Conclusion"]
    assert result["debug"]["rendered_sections"] == ["Problématiques", "Conclusion"]
    assert [item["section"] for item in result["claims"]] == ["Problématiques", "Conclusion"]
    assert "La première formulation distingue" in result["claims"][0]["text"]
    assert "La seconde formulation examine" in result["claims"][0]["text"]


class RequiredEightTurnModel(MultiTurnModel):
    repair_calls = 0

    def plan_legal_chat(self, request, seed_request, prompt_context):
        question = request.question.casefold()
        if "avis détaillé" in question:
            return _plan("LEGAL_OPINION", needs_legal_research=True, needs_case_context=True)
        if "utilisation des biens sociaux" in question:
            return _plan(
                "DRAFT_EDIT", needs_active_draft=True, draft_operation="EXPAND",
                target_section="utilisation des biens sociaux",
            )
        if "distributions de bénéfices" in question:
            return _plan(
                "DRAFT_EDIT", needs_active_draft=True, draft_operation="DELETE",
                target_section="distributions de bénéfices",
            )
        if "contrats et des factures" in question:
            return _plan(
                "NEW_CASE_FACT", secondary_goals=["CASE_CHAT"], needs_case_context=True,
                new_case_facts=["Des contrats et des factures ont été retrouvés."],
            )
        if "intègre-le" in question:
            return _plan(
                "DRAFT_EDIT", needs_active_draft=True, needs_case_context=True,
                draft_operation="UPDATE_WITH_NEW_FACTS",
                target_section="Utilisation des actifs sociaux",
            )
        if "conclusion est trop affirmative" in question:
            return _plan(
                "DRAFT_EDIT", needs_active_draft=True,
                draft_operation="SOFTEN_CONCLUSION", target_section="Conclusion",
            )
        return _plan("SIMPLE_CHAT")

    def generate_legal_reasoning(self, request, evidence_pack, sources):
        return json.dumps({"claims":[
            {"section":"Faits", "text":"Les faits décrits restent à confronter aux pièces du dossier.", "grounding_type":"USER_PROVIDED_FACT", "citations":[]},
            {"section":"Utilisation des actifs sociaux", "text":"L'utilisation alléguée des biens sociaux doit être analysée selon sa finalité, sa chronologie et les justificatifs disponibles.", "grounding_type":"GENERAL_REASONING", "citations":[]},
            {"section":"Conclusion", "text":"L'analyse demeure provisoire dans l'attente des contrats et pièces comptables.", "grounding_type":"MISSING_INFORMATION", "citations":[]},
        ]})

    def generate_conversation(self, request, prompt_context):
        return json.dumps({
            "answer":"Oui. Les contrats et factures peuvent modifier l'analyse en documentant la cause des paiements, leur chronologie et leur intérêt pour la société. Il faut toutefois vérifier leur authenticité et leur cohérence comptable avant de conclure.",
            "current_focus":"impact des contrats et factures",
        })

    def revise_legal_draft(self, request, user_task, prompt_context, sources):
        operation = user_task.draft_operation.value
        if operation == "EXPAND":
            target = "utilisation des biens sociaux"
            content = "Analyse développée de l'utilisation des actifs sociaux : finalité, chronologie, intérêt social, justificatifs, objections et explications alternatives."
            delete = False
        elif operation == "DELETE":
            target, content, delete = "distributions de bénéfices", None, True
        elif operation == "UPDATE_WITH_NEW_FACTS":
            target = "Utilisation des actifs sociaux"
            content = "Les contrats et factures retrouvés doivent être rapprochés de chaque paiement. Ils peuvent étayer une cause économique licite, sous réserve de leur date, de leur authenticité et de leur comptabilisation."
            delete = False
        else:
            target = "Conclusion"
            content = "À ce stade, plusieurs lectures restent possibles. Une conclusion définitive dépendra de la vérification des pièces, de la chronologie et des explications des parties."
            delete = False
        return json.dumps({
            "target_section":target, "replacement_title":None,
            "replacement_content":content, "delete_target":delete,
            "full_rewrite":None, "explanation":"Le passage demandé a été retravaillé.",
        })

    def repair_draft_edit(self, request, user_task, prompt_context, sources, validation_error):
        self.repair_calls += 1
        return self.revise_legal_draft(request, user_task, prompt_context, sources)


def test_required_eight_turn_legal_conversation(client, monkeypatch):
    api, settings = client
    settings.llm_provider = "ollama"
    model = RequiredEightTurnModel()
    monkeypatch.setattr("app.services.rag.engine.llm_provider", lambda settings: model)
    case = api.post("/cases", json={"title":"Société Atlas", "reference":"CHAT-8"}).json()

    def ask(question, conversation_id=None):
        response = api.post("/rag/query", json={
            "question":question, "case_id":case["id"], "scope":"LEGAL_AND_CASE",
            "mode":"LEGAL_OPINION", "conversation_id":conversation_id, "debug":True,
        })
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["user_facing_response"] == result["answer"]
        assert all(key in result for key in (
            "case_updates", "draft_updates", "sources_used", "tool_calls", "follow_up_needed",
        ))
        return result

    turn1 = ask("bonjour")
    assert turn1["response_mode"] == "CHAT"
    turn2 = ask("Voici le dossier de la société. Rédige un avis détaillé.", turn1["conversation_id"])
    draft_id = turn2["draft_id"]
    assert turn2["draft_created"] is True and turn2["draft_version"] == 1

    turn3 = ask("développe uniquement la partie sur l'utilisation des biens sociaux.", turn1["conversation_id"])
    assert turn3["draft_modified"] is True and turn3["draft_version"] == 2
    assert "Section cible introuvable" not in turn3["answer"]

    turn4 = ask("supprime l'argument concernant les distributions de bénéfices.", turn1["conversation_id"])
    assert turn4["draft_modified"] is False and turn4["draft_version"] == 2
    assert "n’apparaît pas" in turn4["answer"]
    assert turn4["follow_up_needed"] is True
    assert turn4["debug"]["self_repair_attempted"] is True

    turn5 = ask("nous avons retrouvé des contrats et des factures. Est-ce que cela change ton analyse ?", turn1["conversation_id"])
    assert turn5["case_state_updated"] is True
    assert turn5["draft_version"] == 2 and turn5["draft_modified"] is False
    assert turn5["case_updates"]

    turn6 = ask("oui, intègre-le.", turn1["conversation_id"])
    assert turn6["draft_modified"] is True and turn6["draft_version"] == 3
    turn7 = ask("la conclusion est trop affirmative.", turn1["conversation_id"])
    assert turn7["modified_section"] == "Conclusion" and turn7["draft_version"] == 4
    turn8 = ask("merci", turn1["conversation_id"])
    assert turn8["response_mode"] == "CHAT" and turn8["answer"] == "Avec plaisir."
    assert api.get(f"/drafts/{draft_id}").json()["version"] == 4
    assert model.repair_calls == 1


def test_semantic_section_resolution_uses_title_and_content():
    sections = [
        {"title":"Cadre général", "content":"Principes introductifs."},
        {"title":"Usage des actifs de la société", "content":"Analyse de l'utilisation des biens sociaux et de l'intérêt social."},
        {"title":"Conclusion", "content":"Appréciation prudente."},
    ]
    assert resolve_draft_section(
        sections, "la partie sur les biens sociaux",
        "développe la partie sur l'utilisation des biens sociaux",
    ) == 1
    assert resolve_draft_section(sections, "distribution de bénéfices", "supprime cet argument") is None


def test_long_form_legal_analysis_contains_full_reasoning_stages(client, monkeypatch):
    api, settings = client
    settings.llm_provider = "ollama"

    class LongFormModel(MultiTurnModel):
        def plan_legal_chat(self, request, seed_request, prompt_context):
            return _plan("LEGAL_OPINION", issue_queries=[request.question], needs_legal_research=True)

        def generate_legal_reasoning(self, request, evidence_pack, sources):
            stages = [
                "Règle et champ d'application", "Conditions à examiner", "Application aux faits",
                "Arguments principaux", "Contre-arguments", "Preuves nécessaires",
                "Stratégie procédurale", "Conclusion prudente",
            ]
            analyses = [
                "La règle applicable doit être identifiée dans une source vérifiée puis reliée à l'obligation discutée.",
                "Les conditions cumulatives et les éventuelles exceptions doivent être isolées avant toute conclusion.",
                "La chronologie communiquée doit être confrontée séparément à chacune de ces conditions.",
                "La position du client gagne à relier chaque argument à une pièce datée et à un effet concret.",
                "La partie adverse peut discuter la causalité, l'imputabilité et la force probante des documents.",
                "Les originaux, échanges contemporains et éléments techniques manquants doivent être rassemblés.",
                "Le choix de la démarche dépendra du contrat, des formalités accomplies et de l'urgence démontrable.",
                "Une position définitive reste prématurée tant que les règles et les pièces décisives ne sont pas vérifiées.",
            ]
            return json.dumps({"claims":[{
                "section":stage,
                "text":((analysis + " ") * 4 + "\n\n" + (f"Pour {stage.casefold()}, l'analyse reste conditionnée par la cohérence des pièces et le débat contradictoire. " * 3)),
                "grounding_type":"GENERAL_REASONING" if index < 7 else "MISSING_INFORMATION",
                "citations":[],
            } for index, (stage, analysis) in enumerate(zip(stages, analyses))]})

    monkeypatch.setattr("app.services.rag.engine.llm_provider", lambda settings: LongFormModel())
    response = api.post("/rag/query", json={
        "question":"Rédige une analyse détaillée complète de la responsabilité possible.",
        "mode":"LEGAL_OPINION", "debug":True,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["claims"]) == 8
    assert len(result["answer"]) > 3500
    assert "Contre-arguments" in result["answer"] and "Preuves nécessaires" in result["answer"]


def test_rag_source_is_used_for_analysis_and_cited(client, monkeypatch):
    api, settings = client
    settings.llm_provider = "ollama"
    source = Source(
        id=uuid4(), source_type="law", document_id=uuid4(), article_id=uuid4(),
        title="Code de test", article_number="42",
        original_text="Toute décision sociale doit être justifiée par l'intérêt de la société et conservée avec ses pièces justificatives.",
        language="fr", page_start=12, page_end=12,
        metadata={"status":"in_force", "legal_text":{"kind":"code"}}, score=4.2,
        file_url="/files/test.pdf",
    )
    issue = LegalIssue(
        id="ISSUE-1", title="Justification de la décision", description="Examiner la justification de la décision sociale",
        legal_questions=["Comment justifier la décision ?"], search_queries=["justification décision sociale"],
        evidence_ids=[f"EVID-{source.id}"], evidence_status="SUPPORTED",
    )
    pack = EvidencePackBuilder().build([issue], [source])

    def retrieve_source(self, *args, **kwargs):
        return pack, [source], [], {"source_relevance_decisions":[{
            "source_id":str(source.id), "issue_id":"ISSUE-1",
            "source_relevance_decision":"USE", "accepted":True, "reason":"accepted",
        }], "issue_retrievals":[], "tool_calls_used":1, "tool_budget_remaining":7}

    class SourceAwareModel(MultiTurnModel):
        def plan_legal_chat(self, request, seed_request, prompt_context):
            return _plan("LEGAL_OPINION", issue_queries=[request.question], needs_legal_research=True)

        def generate_legal_reasoning(self, request, evidence_pack, sources):
            quote = source.original_text
            return json.dumps({"claims":[
                {"section":"Règle vérifiée", "text":"La décision sociale doit être justifiée par l'intérêt de la société et documentée.", "grounding_type":"SOURCE_BACKED", "citations":[{"source_id":str(source.id), "quote":quote}]},
                {"section":"Application", "text":"Il faut rapprocher cette exigence des motifs, de la chronologie et des pièces annoncées, puis tester les explications alternatives.", "grounding_type":"GENERAL_REASONING", "citations":[]},
                {"section":"Conclusion", "text":"La conclusion dépend de l'examen des justificatifs effectivement conservés.", "grounding_type":"MISSING_INFORMATION", "citations":[]},
            ]})

        def verify_claims(self, claims):
            return [{"claim_index":item["claim_index"], "verdict":"supported", "reason":"La citation soutient l'affirmation."} for item in claims]

    monkeypatch.setattr("app.services.rag.engine.EvidenceRetriever.retrieve", retrieve_source)
    monkeypatch.setattr("app.services.rag.engine.llm_provider", lambda settings: SourceAwareModel())
    response = api.post("/rag/query", json={
        "question":"Analyse complètement la justification de cette décision sociale.",
        "mode":"LEGAL_OPINION", "debug":True,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["citations"][0]["id"] == str(source.id)
    assert result["sources_used"] == [str(source.id)]
    assert result["debug"]["source_relevance_decisions"][0]["source_relevance_decision"] == "USE"
    assert "Application" in result["answer"] and source.original_text != result["answer"]


def test_short_validated_opinion_is_expanded_into_direct_legal_draft(client, monkeypatch):
    api, settings = client
    settings.llm_provider = "ollama"

    class SparseOpinionModel(MultiTurnModel):
        def plan_legal_chat(self, request, seed_request, prompt_context):
            return _plan("LEGAL_OPINION", issue_queries=["-"], needs_legal_research=True)

        def generate_legal_reasoning(self, request, evidence_pack, sources):
            return json.dumps({"claims":[
                {"section":"Raisonnement général", "text":"Première analyse prudente.", "grounding_type":"GENERAL_REASONING", "citations":[]},
                {"section":"Faits communiqués", "text":"Les opérations sont présentées comme contestées.", "grounding_type":"USER_PROVIDED_FACT", "citations":[]},
            ]})

        def generate_conversational_fallback(self, request, evidence_pack):
            sections = [
                ("Qualification des flux financiers", "Il faut reconstituer chaque flux, son auteur, son bénéficiaire et sa justification alléguée avant toute qualification."),
                ("Portée des contrats et factures", "Les contrats et factures doivent être rapprochés des livrables, des équipes mobilisées et des écritures comptables correspondantes."),
                ("Lecture possible de l'accusation", "L'accusation pourrait invoquer l'absence de contrepartie et la circulation indirecte des fonds; cette hypothèse reste à éprouver par les pièces."),
                ("Arguments de défense", "La défense devra établir la réalité économique des prestations et expliquer séparément la cause de chaque versement personnel."),
                ("Pièces à vérifier", "Les grands livres, justificatifs bancaires, feuilles de temps et comptes courants annoncés constituent les vérifications prioritaires."),
                ("Stratégie et conclusion prudente", "La stratégie doit partir d'une chronologie chiffrée, tester les incohérences puis réserver toute conclusion jusqu'à l'examen des originaux."),
            ]
            return json.dumps({"claims":[{
                "section":title,
                "text":text,
                "grounding_type":"GENERAL_REASONING" if index < 5 else "MISSING_INFORMATION",
                "citations":[],
            } for index, (title, text) in enumerate(sections)]})

    monkeypatch.setattr("app.services.rag.engine.llm_provider", lambda settings: SparseOpinionModel())
    response = api.post("/rag/query", json={
        "question":"Rédige un avis juridique complet sur les mouvements financiers contestés.",
        "mode":"LEGAL_OPINION", "debug":True,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["claims"]) >= 6
    assert result["confidence"]["citation_coverage"]["semantic_entailment"]["fallback_used"] is True
    assert "Questions juridiques pertinentes" not in result["answer"]
    assert "Raisonnement général" not in result["answer"]
    assert result["user_task"]["issue_queries"] == [result["user_task"]["original_message"]]
