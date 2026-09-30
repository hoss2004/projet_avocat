from datetime import date
from uuid import uuid4
import json
import pytest
from sqlalchemy import select
from app.models.knowledge import SearchChunk
from app.schemas.assistant import Source, GeneratedAnswer, RagRequest
from app.services.citations.validator import CitationValidator, CitationError
from app.services.retrieval.lexicon import expand_query
from app.services.retrieval.search import rrf
from app.services.ingestion.cases import paragraph_chunks
from test_ingestion_api import client, upload
from test_pdf import make_pdf

def test_query_expansion_is_multilingual():
    assert "العقلة التحفظية" in expand_query("conditions de la saisie conservatoire")
    assert "saisie conservatoire" in expand_query("شروط العقلة التحفظية")

def test_rrf_rewards_agreement():
    a,b=SearchChunk(id=uuid4()),SearchChunk(id=uuid4())
    ranking=sorted(rrf([[a,b],[b]]),key=lambda x:x[1],reverse=True)
    assert ranking[0][0].id==b.id

def test_original_paragraphs_and_page_boundaries():
    pages=["TEST LAW\nFirst paragraph.\n\nSecond paragraph.","TEST ARTICLE next page"]
    for page,start,end,text in paragraph_chunks(pages,20):
        assert pages[page-1][start:end]==text
    assert {p for p,*_ in paragraph_chunks(pages)}=={1,2}

def source():
    return Source(id=uuid4(),source_type="law",document_id=uuid4(),title="TEST LAW",article_number="403",original_text="Article 403. TEST ARTICLE fictif, sans valeur juridique.",language="fr",page_start=1,page_end=1,metadata={},score=1,file_url="/test")

def answer(s,quote=None,text="Le TEST ARTICLE 403 est fictif."):
    return GeneratedAnswer.model_validate({"claims":[{"section":"Sources","text":text,"citations":[{"source_id":str(s.id),"quote":quote or s.original_text}]}]})

def test_citations_reject_missing_sources_quotes_and_numbers():
    s=source()
    assert CitationValidator().validate(answer(s),[s])
    with pytest.raises(CitationError): CitationValidator().validate(answer(s),[])
    with pytest.raises(CitationError): CitationValidator().validate(answer(s,quote="Citation totalement inventée"),[s])
    with pytest.raises(CitationError): CitationValidator().validate(answer(s,text="Le délai est de 99 jours."),[s])
    with pytest.raises(CitationError): CitationValidator().validate(answer(s,text="Voir l’article 999."),[s])


def test_local_decoding_only_offers_actual_source_quote_pairs():
    from app.services.llm.providers import grounded_schema
    a,b=source(),source()
    b.original_text="TEST ARTICLE B. " + "Long original text. "*40
    schema=grounded_schema([a,b])
    assert schema["properties"]["claims"]["minItems"]==1
    assert schema["properties"]["claims"]["maxItems"]==10
    source_claim=schema["$defs"]["Claim"]["oneOf"][0]
    assert source_claim["properties"]["text"]["maxLength"]==1800
    assert source_claim["properties"]["grounding_type"]["const"]=="SOURCE_BACKED"
    assert source_claim["properties"]["citations"]["minItems"]==1
    assert schema["$defs"]["Claim"]["oneOf"][1]["properties"]["citations"]["maxItems"]==0
    opinion_schema=grounded_schema([a,b],3,opinion=True)
    assert opinion_schema["properties"]["claims"]["minItems"]==6
    assert "prefixItems" not in opinion_schema["properties"]["claims"]
    fallback_schema=grounded_schema([],3,opinion=True,allowed_non_source=["GENERAL_REASONING","MISSING_INFORMATION"])
    assert fallback_schema["$defs"]["Claim"]["oneOf"][0]["properties"]["grounding_type"]["enum"]==["GENERAL_REASONING","MISSING_INFORMATION"]
    variants=schema["$defs"]["Quote"]["anyOf"]
    assert len(variants)==2
    for variant,original in zip(variants,[a,b]):
        assert variant["properties"]["source_id"]["const"]==str(original.id)
        for quote in variant["properties"]["quote"]["enum"]:
            assert 8 <= len(quote) <= 180
            assert quote in original.original_text

def test_exact_search_and_empty_rag(client):
    api,settings=client
    settings.embedding_provider="disabled";settings.llm_provider="disabled"
    empty=api.post("/rag/query",json={"question":"saisie conservatoire"})
    assert empty.status_code==200,empty.text
    assert empty.json()["retrieved_sources"]==[]
    assert empty.json()["missing_information"]
    document=upload(api).json()["id"]
    api.post("/legal-documents/ingest",json={"document_id":document})
    response=api.post("/search/legal",json={"question":"الفصل ٤٠٣"})
    assert response.status_code==200,response.text
    assert response.json()["sources"][0]["article_number"]=="403"
    result=api.post("/rag/query",json={"question":"article 403"}).json()
    assert result["mode"]=="exact_reference"
    assert result["status"]=="FOUND"
    assert result["claims"]==[]
    assert result["retrieved_sources"]
    # Explicit date excludes statutes with unknown validity rather than inventing dates.
    result=api.post("/search/legal",json={"question":"article 403","at_date":"2021-01-01"}).json()
    assert result["sources"]==[]

def test_case_isolation_and_timeline(client):
    api,settings=client
    settings.embedding_provider="disabled";settings.llm_provider="disabled"
    a=api.post("/cases",json={"title":"TEST CASE A","reference":"A"}).json()["id"]
    b=api.post("/cases",json={"title":"TEST CASE B","reference":"B"}).json()["id"]
    pdf=make_pdf("TEST LAW - SYNTHETIC\nContrat confidentiel ALPHA du 12/03/2024.")
    result=api.post(f"/cases/{a}/documents",files={"file":("TEST.pdf",pdf,"application/pdf")})
    assert result.status_code==200,result.text
    document=result.json()["id"]
    assert api.get(f"/cases/{a}/timeline").json()[0]["event_date"]=="2024-03-12"
    assert api.get(f"/cases/{b}/documents/{document}/file").status_code==404
    assert api.post("/search/case",json={"question":"ALPHA","scope":"CASE_ONLY","case_id":b}).json()["sources"]==[]
    assert api.post("/search/legal",json={"question":"ALPHA"}).json()["sources"]==[]
    result=api.post("/search/case",json={"question":"ALPHA","scope":"CASE_ONLY","case_id":a})
    assert len(result.json()["sources"])==1
    assert api.post("/rag/query",json={"question":"ALPHA","scope":"CASE_ONLY"}).status_code==422
    settings.tenant_id=uuid4()
    assert api.get(f"/cases/{a}").status_code==404
    assert api.post("/search/case",json={"question":"ALPHA","scope":"CASE_ONLY","case_id":a}).status_code==404
    assert api.get("/cases").json()==[]

def test_rag_validates_model_output_and_falls_back(client,monkeypatch):
    api,settings=client
    settings.embedding_provider="disabled"
    doc=upload(api).json()["id"]
    api.post("/legal-documents/ingest",json={"document_id":doc})
    class Model:
        def verify_claims(self,claims):
            return [{"claim_index":c["claim_index"],"verdict":"supported","reason":"synthetic test"} for c in claims]
        def generate(self,request,sources):
            s=sources[0]
            return json.dumps({"claims":[{"section":"Test","text":"Le texte cité est fictif.","citations":[{"source_id":str(s.id),"quote":s.original_text[:40]}]}]})
    monkeypatch.setattr("app.services.rag.engine.llm_provider",lambda settings:Model())
    result=api.post("/rag/query",json={"question":"texte artificiel pour les tests"}).json()
    assert result["mode"]=="generated"
    assert result["confidence"]["citation_coverage"]["verified_claims"]==1
    class BadModel:
        def generate(self,*args):
            return '{"claims":[{"section":"fake","text":"article 999","citations":[{"source_id":"fake","quote":"fabricated quote"}]}]}'
    monkeypatch.setattr("app.services.rag.engine.llm_provider",lambda settings:BadModel())
    result=api.post("/rag/query",json={"question":"texte artificiel pour les tests"}).json()
    assert result["mode"]=="extracts_only"
    assert result["claims"]==[]
    assert "999" not in result["answer"]
