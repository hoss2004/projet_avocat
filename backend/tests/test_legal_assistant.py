import json
from uuid import uuid4

from app.schemas.assistant import GeneratedAnswer, RagRequest, Source
from app.services.citations.claims import ClaimSourceValidator
from app.services.legal_assistant.evidence import EvidencePackBuilder
from app.services.legal_assistant.intent import LegalIntentRouter
from app.services.legal_assistant.issues import LegalIssueAnalyzer
from app.services.legal_assistant.models import (
    ClaimSupportStatus,
    EvidenceSourceType,
    LegalIntent,
)
from app.services.legal_assistant.orchestrator import LegalChatOrchestrator
from app.services.legal_assistant.reasoning import LegalReasoningService
from test_ingestion_api import client, upload


def synthetic_source(source_type="law", document_type="code"):
    return Source(
        id=uuid4(),
        source_type=source_type,
        document_id=uuid4(),
        case_id=uuid4() if source_type == "case_document" else None,
        title="SYNTHETIC TEST SOURCE",
        article_number="T-12" if source_type == "law" else None,
        original_text="Synthetic provision: the documented condition requires written notice before the test action.",
        language="en",
        page_start=2,
        page_end=2,
        metadata={"document_type": document_type, "status": "test"},
        score=2,
        file_url="/synthetic",
    )


def test_intent_router_separates_outcome_from_explicit_references():
    router = LegalIntentRouter()
    exact = router.route(RagRequest(question="article 12"))
    assert exact.user_request.primary_intent == LegalIntent.EXACT_REFERENCE_QUERY
    assert [item.article_number for item in exact.user_request.explicit_references] == ["12"]

    opinion = router.route(RagRequest(question="Donne un avis juridique sur le risque au regard de l'article 12"))
    assert opinion.user_request.primary_intent == LegalIntent.LEGAL_OPINION
    assert [item.article_number for item in opinion.user_request.explicit_references] == ["12"]
    assert opinion.exact_reference_query is not None

    drafting = router.route(RagRequest(question="Rédige une mise en demeure fondée sur l'article 12 et l'article 13"))
    assert drafting.user_request.primary_intent == LegalIntent.LEGAL_DRAFTING
    assert [item.article_number for item in drafting.user_request.explicit_references] == ["12", "13"]


def test_rédige_un_avis_is_an_opinion_not_a_generic_draft():
    request = RagRequest(
        question=(
            "Je représente ALPHA dans un contrat de prestations. BETA conteste les manquements. "
            "Rédige un véritable avis juridique complet et analytique sur la résiliation."
        )
    )
    decision = LegalIntentRouter().route(request)
    assert decision.user_request.primary_intent == LegalIntent.LEGAL_OPINION


def test_contract_opinion_ignores_stale_planner_prompt_and_decomposes_research():
    request = RagRequest(
        question=(
            "Je représente ALPHA. Le contrat de prestations impose des rapports mensuels. "
            "BETA est en retard et conteste la mise en demeure. Les factures sont impayées. "
            "Je veux un avis juridique complet sur les obligations, l'inexécution et la résiliation."
        )
    )
    seed = LegalIntentRouter().route(request)

    class StalePlanner:
        def plan_legal_chat(self, current_request, seed_request, prompt_context):
            return json.dumps({
                "primary_goal":"LEGAL_DRAFTING", "secondary_goals":[],
                "issue_queries":["SYSTEM\nSOURCES JURIDIQUES DE TEST\nArticle TEST-101"],
                "requested_transformations":[], "mentioned_documents":[],
                "needs_legal_research":True, "needs_case_law":False,
                "needs_case_documents":False, "needs_active_draft":False,
                "needs_case_context":False, "new_case_facts":[],
                "create_new_case":False, "response_strategy":"test",
            })

    decision = LegalChatOrchestrator().understand(request, None, StalePlanner(), seed, {})
    assert decision.user_task.primary_goal == LegalIntent.LEGAL_OPINION
    assert len(decision.user_task.issue_queries) == 3
    assert all("TEST" not in query for query in decision.user_task.issue_queries)
    assert any("mise en demeure" in query for query in decision.user_task.issue_queries)


def test_issue_analyzer_creates_independent_retrieval_queries():
    decision = LegalIntentRouter().route(RagRequest(
        question="La notification est-elle valable ? Le délai est-il respecté ? Quels arguments opposer ?"
    ))
    issues = LegalIssueAnalyzer().analyze(decision.user_request)
    assert len(issues) == 3
    assert [item.id for item in issues] == ["ISSUE-1", "ISSUE-2", "ISSUE-3"]
    assert all(item.search_queries == [item.description] for item in issues)


def test_evidence_pack_preserves_source_type_hierarchy_and_issue_links():
    decision = LegalIntentRouter().route(RagRequest(question="Analyse juridique du test"))
    issue = LegalIssueAnalyzer().analyze(decision.user_request)[0]
    law = synthetic_source()
    case_document = synthetic_source("case_document", "contract")
    issue = issue.model_copy(update={
        "evidence_ids": [f"EVID-{law.id}", f"EVID-{case_document.id}"],
        "evidence_status": "SUPPORTED",
    })
    pack = EvidencePackBuilder().build([issue], [law, case_document], explicit_source_ids={str(law.id)})
    assert pack.statutes[0].source_type == EvidenceSourceType.STATUTE
    assert pack.statutes[0].is_explicit_reference is True
    assert pack.case_documents[0].source_type == EvidenceSourceType.CONTRACT
    assert pack.statutes[0].issue_ids == ["ISSUE-1"]


def test_claim_source_validator_reports_all_support_statuses():
    source = synthetic_source()
    quote = source.original_text
    answer = GeneratedAnswer.model_validate({"claims": [
        {"section":"Test", "text":"The condition requires written notice.", "citations":[{"source_id":str(source.id), "quote":quote}]},
        {"section":"Test", "text":"Only part of the proposition is documented.", "citations":[{"source_id":str(source.id), "quote":quote}]},
        {"section":"Test", "text":"The source says the opposite.", "citations":[{"source_id":str(source.id), "quote":quote}]},
        {"section":"Test", "text":"This conclusion lacks support.", "citations":[{"source_id":str(source.id), "quote":quote}]},
        {"section":"Test", "text":"Unknown evidence.", "citations":[{"source_id":str(uuid4()), "quote":quote}]},
    ]})

    class Verifier:
        def verify_claims(self, claims):
            verdicts = ["supported", "partially_supported", "contradicted", "unsupported"]
            return [
                {"claim_index": item["claim_index"], "verdict": verdicts[index], "reason":"synthetic assessment"}
                for index, item in enumerate(claims)
            ]

    statuses = [item.support_status for item in ClaimSourceValidator().assess(answer, [source], Verifier())]
    assert statuses == [
        ClaimSupportStatus.SUPPORTED,
        ClaimSupportStatus.PARTIALLY_SUPPORTED,
        ClaimSupportStatus.CONTRADICTED,
        ClaimSupportStatus.UNSUPPORTED,
        ClaimSupportStatus.NO_SOURCE,
    ]


def test_reasoning_service_filters_claims_that_are_not_fully_supported():
    request = RagRequest(question="Donne un avis juridique synthétique", mode="LEGAL_OPINION", language="en")
    decision = LegalIntentRouter().route(request)
    issue = LegalIssueAnalyzer().analyze(decision.user_request)[0]
    source = synthetic_source()
    issue = issue.model_copy(update={"evidence_ids":[f"EVID-{source.id}"], "evidence_status":"SUPPORTED"})
    pack = EvidencePackBuilder().build([issue], [source])

    class Model:
        def generate_legal_reasoning(self, generated_request, evidence_pack, sources):
            assert generated_request.mode == "LEGAL_OPINION"
            assert evidence_pack.legal_issues[0].id == "ISSUE-1"
            return json.dumps({"claims":[
                {"section":"Supported", "text":"Written notice is required.", "citations":[{"source_id":str(source.id), "quote":source.original_text}]},
                {"section":"Partial", "text":"A broader unsupported proposition.", "citations":[{"source_id":str(source.id), "quote":source.original_text}]},
            ]})

        def verify_claims(self, claims):
            return [
                {"claim_index":item["claim_index"], "verdict":"supported" if index == 0 else "partially_supported", "reason":"synthetic"}
                for index, item in enumerate(claims)
            ]

    generated, verification = LegalReasoningService().generate(
        request, decision.user_request, pack, [source], Model()
    )
    assert [claim.section for claim in generated.claims] == ["Supported"]
    assert verification["accepted_claims"] == 1
    assert verification["verdicts"][1]["support_status"] == "PARTIALLY_SUPPORTED"


def test_reasoning_service_salvages_local_model_json_with_extra_fields():
    raw = json.dumps({"claims":[{
        "section":"Analyse", "text":"Une analyse prudente fondée sur les pièces.",
        "citations":[], "unexpected":"ignored",
    }], "comment":"ignored"})
    parsed = LegalReasoningService()._parse_generated(raw)
    assert len(parsed.claims) == 1
    assert parsed.claims[0].grounding_type == "GENERAL_REASONING"
    assert parsed.claims[0].text == "Une analyse prudente fondée sur les pièces."


def test_reasoning_service_salvages_complete_claims_from_truncated_json():
    raw = '{"claims":[{"section":"Analyse","text":"Paragraphe complet et prudent pour le dossier.","grounding_type":"GENERAL_REASONING","citations":[]},{"section":"Coupé","text":"phrase'
    parsed = LegalReasoningService()._parse_generated(raw)
    assert len(parsed.claims) == 1
    assert parsed.claims[0].section == "Analyse"


def test_cross_language_opinion_uses_one_qualified_generation_and_keeps_original_sources():
    request=RagRequest(question="Rédige un avis juridique complet sur le contrat.",mode="LEGAL_OPINION",language="fr")
    decision=LegalIntentRouter().route(request)
    issue=LegalIssueAnalyzer().analyze(decision.user_request)[0]
    source=synthetic_source();source.language="ar"
    issue=issue.model_copy(update={"evidence_ids":[f"EVID-{source.id}"],"evidence_status":"SUPPORTED"})
    pack=EvidencePackBuilder().build([issue],[source])

    class CrossLanguageModel:
        def generate_legal_reasoning(self,*args):
            raise AssertionError("The expensive grounded generation must be skipped")
        def generate_conversational_fallback(self,request,evidence_pack):
            texts=[
                "Les obligations annoncées doivent être attribuées à chaque partie puis confrontées au contrat complet et à ses annexes.",
                "Les retards et rapports absents appellent une chronologie par intervention, avec bons techniques et échanges contemporains.",
                "Les mises en demeure doivent être comparées à la clause invoquée, à leur réception et aux régularisations intervenues ensuite.",
                "L'empêchement allégué suppose d'examiner les demandes d'accès, les informations techniques attendues et leur incidence sur chaque retard.",
                "Les factures exigent un contrôle de leur échéance, de leur acceptation, de leur contestation et des prestations correspondantes.",
                "La décision finale dépendra d'une matrice des preuves, des contre-arguments et des risques avant toute rupture du contrat.",
            ]
            return json.dumps({"claims":[{
                "section":f"Axe {index}",
                "text":text,
                "grounding_type":"GENERAL_REASONING", "citations":[],
            } for index,text in enumerate(texts,1)]})

    generated,verification=LegalReasoningService().generate(
        request,decision.user_request,pack,[source],CrossLanguageModel(),
    )
    assert len(generated.claims)==7
    assert generated.claims[-1].grounding_type=="SOURCE_BACKED"
    assert generated.claims[-1].citations[0].source_id==str(source.id)
    assert verification["method"]=="cross_language_original_text_with_qualified_analysis"
    assert verification["fallback_used"] is True


def test_contract_dispute_safety_template_preserves_party_roles():
    question=(
        "Je représente ALPHA. Le contrat impose à BETA des interventions et des rapports. "
        "ALPHA reproche des retards à BETA et lui a envoyé deux mises en demeure. "
        "BETA soutient que ALPHA a empêché une intervention et que des factures sont impayées. "
        "ALPHA envisage une résiliation."
    )
    rows=LegalReasoningService()._contract_dispute_claims(question)
    assert len(rows)==8
    text="\n".join(row["text"] for row in rows)
    assert "ALPHA affirme avoir envoyé deux mises en demeure à BETA" in text
    assert "BETA soutient que ALPHA" in text
    assert "BETA a adressé" not in text
    assert "aucune conséquence pénale" in text


def test_complex_request_uses_exact_reference_as_evidence_not_primary_intent(client):
    api, settings = client
    document_id = upload(api).json()["id"]
    api.post("/legal-documents/ingest", json={"document_id": document_id})
    response = api.post("/rag/query", json={
        "question":"Donne un avis juridique sur le risque au regard de l'article 403",
        "mode":"LEGAL_OPINION",
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["primary_intent"] == "LEGAL_OPINION"
    assert result["mode"] == "extracts_only"
    assert result["retrieved_sources"][0]["article_number"] == "403"
    assert result["evidence_pack"]["explicit_references"][0]["article_number"] == "403"
    assert result["legal_issues"][0]["evidence_status"] == "SUPPORTED"


def test_llm_orchestrator_builds_multi_goal_tool_plan():
    request = RagRequest(
        question="Sur la base de l'article 12, analyse les faits, compare les arguments et rédige un avis",
        mode="LEGAL_OPINION",
    )
    seed = LegalIntentRouter().route(request)

    class Planner:
        def plan_legal_chat(self, current_request, seed_request):
            assert seed_request.explicit_references[0].article_number == "12"
            return json.dumps({
                "primary_goal":"LEGAL_OPINION",
                "secondary_goals":["CASE_ANALYSIS", "ARGUMENTATION", "LEGAL_RESEARCH"],
                "issue_queries":["validité de la notification", "arguments des parties"],
                "requested_document_type":"LEGAL_OPINION",
                "requested_transformations":[],
                "mentioned_documents":["contrat annoncé"],
                "needs_legal_research":True,
                "needs_case_law":False,
                "needs_case_documents":False,
                "needs_active_draft":False,
                "response_strategy":"Rédiger un avis argumenté et signaler les faits non documentés.",
            })

    decision = LegalChatOrchestrator().understand(request, None, Planner(), seed)
    assert decision.llm_orchestrator_called is True
    assert decision.user_task.primary_goal == LegalIntent.LEGAL_OPINION
    assert decision.user_task.secondary_goals == [
        LegalIntent.CASE_ANALYSIS, LegalIntent.ARGUMENTATION, LegalIntent.LEGAL_RESEARCH
    ]
    assert [call.name.value for call in decision.user_task.tool_calls] == [
        "lookup_exact_reference", "search_legal_sources", "search_legal_sources"
    ]


def test_conversational_opinion_is_generated_without_reliable_source(client, monkeypatch):
    api, settings = client
    settings.llm_provider = "ollama"

    class ConversationalModel:
        def plan_legal_chat(self, request, seed_request):
            return json.dumps({
                "primary_goal":"LEGAL_OPINION",
                "secondary_goals":["CASE_ANALYSIS", "ARGUMENTATION"],
                "issue_queries":["qualification des faits communiqués", "preuves à rechercher"],
                "requested_document_type":"LEGAL_OPINION",
                "requested_transformations":[],
                "mentioned_documents":["contrat non versé"],
                "needs_legal_research":False,
                "needs_case_law":False,
                "needs_case_documents":False,
                "needs_active_draft":False,
                "response_strategy":"Produire un premier avis prudent à partir des faits racontés.",
            })

        def generate_legal_reasoning(self, request, evidence_pack, sources):
            assert not sources
            return json.dumps({"claims":[
                {"section":"Faits communiqués", "text":"Selon votre récit, un contrat existe mais il n'est pas encore versé au dossier.", "grounding_type":"USER_PROVIDED_FACT", "citations":[]},
                {"section":"Analyse préliminaire", "text":"Il faut distinguer la réalité de l'engagement, son exécution alléguée et les objections prévisibles de l'autre partie.", "grounding_type":"GENERAL_REASONING", "citations":[]},
                {"section":"Éléments à obtenir", "text":"Le contrat annoncé et les échanges relatifs à son exécution doivent être examinés avant une conclusion ferme.", "grounding_type":"MISSING_INFORMATION", "citations":[]},
            ]})

    monkeypatch.setattr("app.services.rag.engine.llm_provider", lambda settings: ConversationalModel())
    response = api.post("/rag/query", json={
        "question":"Voici les faits : nous avons un contrat qui n'est pas encore uploadé. Rédige un avis juridique très analytique.",
        "mode":"LEGAL_OPINION",
        "debug":True,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["mode"] == "generated"
    assert result["primary_goal"] == "LEGAL_OPINION"
    assert len(result["claims"]) == 3
    assert "passages retrouvés" not in result["answer"].casefold()
    assert result["debug"]["llm_orchestrator_called"] is True
    assert result["debug"]["reasoning_llm_called"] is True
    assert result["debug"]["tool_calls"] == []
    assert result["debug"]["sources_selected_count"] == 0
    assert result["debug"]["claim_validation_called"] is True
    assert result["debug"]["final_response_type"] == "LEGAL_OPINION"
    assert {item["support_status"] for item in result["claim_validation"]} == {"NO_SOURCE"}


def test_rejected_positive_law_falls_back_to_qualified_conversation():
    request = RagRequest(question="Rédige un avis prudent", mode="LEGAL_OPINION")
    decision = LegalIntentRouter().route(request)
    issue = LegalIssueAnalyzer().analyze(decision.user_request)[0]
    source = synthetic_source()
    issue = issue.model_copy(update={"evidence_ids":[f"EVID-{source.id}"], "evidence_status":"SUPPORTED"})
    pack = EvidencePackBuilder().build([issue], [source])

    class Model:
        def generate_legal_reasoning(self, request, evidence_pack, sources):
            return json.dumps({"claims":[{
                "section":"Règle rejetée",
                "text":"L'article 999 impose un délai de 77 jours.",
                "grounding_type":"SOURCE_BACKED",
                "citations":[{"source_id":str(source.id), "quote":source.original_text}],
            }]})

        def generate_conversational_fallback(self, request, evidence_pack):
            return json.dumps({"claims":[
                {"section":"Analyse", "text":"La qualification dépend des engagements effectivement convenus et de leur exécution démontrable.", "grounding_type":"GENERAL_REASONING", "citations":[]},
                {"section":"Arguments", "text":"Il convient de confronter la position du client aux objections prévisibles de l'autre partie.", "grounding_type":"GENERAL_REASONING", "citations":[]},
                {"section":"À vérifier", "text":"Les documents contractuels et les échanges utiles doivent être examinés avant une conclusion ferme.", "grounding_type":"MISSING_INFORMATION", "citations":[]},
            ]})

    generated, verification = LegalReasoningService().generate(
        request, decision.user_request, pack, [source], Model()
    )
    assert len(generated.claims) == 3
    assert verification["method"] == "qualified_conversational_fallback"
    assert verification["fallback_used"] is True
    assert {item["support_status"] for item in verification["verdicts"]} == {"UNSUPPORTED"}
