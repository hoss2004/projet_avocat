import pytest
from app.parsers.legal_code_parser import parse_legal_code
from app.services.citations.claims import ClaimSourceValidator
from app.services.citations.validator import CitationValidator,CitationError
from app.services.retrieval.quality import RetrievalQualityGate
from app.core.config import Settings
from test_assistant import source,answer
from test_ingestion_api import client,upload
from uuid import uuid4

def test_composite_distinguishes_code_and_amending_law():
    text="Code des obligations et des contrats\nDu bail\nArticle 1. TEST loyer.\nArticle 2. TEST bail.\n\nLoi n° 2025-12 du 01/01/2025\nArticle 1. TEST modification.\nArticle 2. TEST autre."
    rows=parse_legal_code([text]).articles
    assert len(rows)==4
    assert rows[0].legal_text["id"]==rows[1].legal_text["id"]
    assert rows[2].legal_text["id"]==rows[3].legal_text["id"]!=rows[0].legal_text["id"]
    assert rows[0].legal_subdomain=="lease" and rows[2].legal_subdomain is None
    assert rows[2].legal_text["title"].startswith("Loi n°")

def test_composite_ambiguous_restart_not_assigned_to_main_code():
    rows=parse_legal_code(["Article 1. TEST\nArticle 20. TEST\nArticle 1. TEST annexe"]).articles
    assert rows[-1].legal_text["kind"]=="unidentified"
    assert rows[-1].legal_text["review_required"]

def test_arabic_composite_and_inline_law_reference():
    text="مجلة الشغل\nالفصل 1\nنص\nالفصل 2\nبمقتضى قانون عدد 12 لسنة 2025\nنص\n\nقانون عدد 12 لسنة 2025\nالفصل 1\nنص جديد"
    rows=parse_legal_code([text]).articles
    assert len(rows)==3
    assert rows[0].legal_text==rows[1].legal_text
    assert rows[2].legal_text["title"]=="قانون عدد 12 لسنة 2025"

@pytest.mark.parametrize("claim",["Le délai est de 30 mois.","La sanction est de 30 %.","Le montant est de 30 TND.","Le contrat date du 03/02/2025."])
def test_number_units_dates_cannot_be_recombined(claim):
    s=source();s.original_text="Le délai est de 30 jours. Le contrat date du 02/03/2025."
    with pytest.raises(CitationError): CitationValidator().validate(answer(s,text=claim),[s])

def test_uncited_article_cannot_authorize_reference():
    s=source();other=source();other.article_number="999"
    with pytest.raises(CitationError): CitationValidator().validate(answer(s,text="Voir article 999."),[s,other])

def test_quote_not_entire_article_is_numeric_evidence():
    s=source();s.original_text="Le loyer est payable. Autre cas : délai de 30 jours."
    with pytest.raises(CitationError): CitationValidator().validate(answer(s,quote="Le loyer est payable.",text="Le délai est de 30 jours."),[s])

@pytest.mark.parametrize("verdict",["contradicted","insufficient"])
def test_semantic_reviewer_rejects_unsupported_claim(verdict):
    s=source();s.original_text="La résiliation est interdite sans notification préalable."
    class Reviewer:
        def verify_claims(self,claims):
            assert claims[0]["evidence"][0]["quote"]==s.original_text
            return [{"claim_index":0,"verdict":verdict}]
    with pytest.raises(CitationError): ClaimSourceValidator().validate(answer(s,text="La résiliation est automatique sans notification."),[s],Reviewer())

def test_semantic_reviewer_fails_closed_and_verbatim_is_supported():
    s=source()
    assert ClaimSourceValidator().validate(answer(s,text=s.original_text),[s],object())["method"]=="verbatim"
    with pytest.raises(CitationError): ClaimSourceValidator().validate(answer(s),[s],object())

def test_quality_gate_blocks_offtopic_and_marks_relevant_unidentified_for_review():
    s=source();s.metadata={"domain":"tax_law","legal_text":{"kind":"main"}}
    settings=Settings(_env_file=None,api_key="test-only-key-with-at-least-32-characters",tenant_id=uuid4())
    selected,report=RetrievalQualityGate().evaluate("loyers impayés et résiliation du bail",[s],settings)
    assert selected==[] and report["status"]=="NO_RELIABLE_SOURCE"
    s.metadata={"legal_text":{"kind":"unidentified"}}
    selected,report=RetrievalQualityGate().evaluate("article 403",[s],settings)
    assert selected==[s]
    assert report["checks"][0]["reason"]=="unidentified_composite_text"
    assert report["checks"][0]["source_relevance_decision"]=="MAYBE"

def test_index_status_is_tenant_scoped_and_corpus_filter_works(client):
    api,settings=client
    doc=upload(api).json()["id"]
    api.post("/legal-documents/ingest",json={"document_id":doc})
    api.post("/search/legal",json={"question":"Quelle règle juridique est applicable ?"})
    status=api.get("/search/index/status").json()
    assert status["documents_count"]==1 and status["articles_count"]==2
    assert status["documents"][0]["indexed"]==2
    assert not status["cabinet_memory"]["enabled"]
    result=api.post("/search/legal",json={"question":"article 403","legal_corpus":"jurisprudence"}).json()
    assert result["sources"]==[]
    settings.tenant_id=uuid4()
    assert api.get("/search/index/status").json()["documents_count"]==0


def test_promulgation_then_book_keeps_code_identity_without_mixing_articles():
    text="مجلة الالتزامات والعقود\n\n1906 ديسمبر 15 أمر مؤرخ في\nالفصل الأول\nنص التقديم\nالفصل 2\nنص\nالكتاب الأول\nفي الكراء\nالفصل الأول\nنص الكراء"
    rows=parse_legal_code([text]).articles
    assert rows[0].legal_text["kind"]=="instrument"
    assert rows[-1].legal_text["kind"]=="code"
    assert rows[-1].legal_subdomain=="lease"
    assert rows[-1].legal_text["title"]=="مجلة الالتزامات والعقود"


def test_promulgation_is_not_the_code_it_introduces():
    from app.services.retrieval.understanding import source_identity
    domain,code=source_identity("Loi introduisant le Code de procedure civile",{"domain":"civil_procedure","code_id":"cpcc","legal_text":{"kind":"instrument"}})
    assert domain=="civil_procedure" and code is None


def test_header_only_citation_cannot_support_legal_conclusion():
    s=source();s.original_text="796 الفصل\nنص قانوني يتعلق بالكراء"
    class WrongReviewer:
        def verify_claims(self,claims):
            raise AssertionError("Headers must be rejected before model review")
    with pytest.raises(CitationError): ClaimSourceValidator().validate(answer(s,quote="796 الفصل",text="Le bail peut être résilié sans préavis."),[s],WrongReviewer())

def test_supported_verdict_with_uncertain_reason_is_rejected():
    s=source()
    class Reviewer:
        def verify_claims(self,claims):
            return [{"claim_index":0,"verdict":"supported","reason":"Cependant la citation ne mentionne pas cette conclusion."}]
    with pytest.raises(CitationError): ClaimSourceValidator().validate(answer(s),[s],Reviewer())


def test_incomplete_model_review_fails_each_missing_claim_closed():
    s=source()
    generated=answer(s)
    class IncompleteReviewer:
        def verify_claims(self,claims):
            return []
    assessment=ClaimSourceValidator().assess(generated,[s],IncompleteReviewer())
    assert assessment[0].support_status.value == "UNSUPPORTED"
    assert "pas rendu de décision complète" in assessment[0].reason


@pytest.mark.parametrize("claim", [
    "Une résiliation abusive pourrait entraîner des sanctions pénales.",
    "ALPHA peut être tenue responsable du préjudice de BETA.",
    "Ce manquement engage la responsabilité contractuelle de BETA.",
])
def test_uncited_legal_consequences_are_not_general_reasoning(claim):
    generated = answer(source(), text=claim)
    generated.claims[0].grounding_type = "GENERAL_REASONING"
    generated.claims[0].citations = []
    assessment = ClaimSourceValidator().assess(generated, [], object())[0]
    assert assessment.support_status.value == "UNSUPPORTED"
