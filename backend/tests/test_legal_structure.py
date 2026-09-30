from uuid import uuid4
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.parsers.legal_code_parser import parse_legal_code
from app.parsers.legal_structure import LegalStructureParser
from app.services.retrieval.understanding import LegalQueryAnalyzer
from app.services.retrieval.config import RetrievalConfig
from app.services.reranking.providers import LegalDomainReranker
from app.services.retrieval.search import retrieve
from app.models.legal import Base
from app.core.config import Settings
from app.schemas.assistant import SearchRequest
from test_retrieval_domains import chunk
from test_ingestion_api import client, upload

QUESTION="La société BETA met ALPHA en demeure de payer trois mois de loyers impayés et menace de demander la résiliation du contrat de location."

def test_structure_propagates_across_pages_and_resets():
    pages=["البيع\nالفصل 100\nنص البيع\nالفصل 101\nنص آخر\nفي الكراء\nالفصل 200\nنص الكراء", "الفصل 201\nنص الكراء\nالمقالة الرابعة\nفي الوكالة\nالفصل 300\nنص الوكالة"]
    result=parse_legal_code(pages)
    assert [a.legal_subdomain for a in result.articles]==["sale","sale","lease","lease","mandate"]
    assert result.articles[3].page_start==2
    original="\n\f\n".join(pages)
    for article in result.articles:
        assert article.original_text==original[article.start_offset:article.end_offset]
    assert result.articles[2].section_ar=="في الكراء"

def test_numbered_headings_blank_lines_and_internal_parts():
    text="المقالة الثالثة\n\nفي الكراء\nالباب الأول\n\nفي الكراء\nالقسم الأول\nالجزء الأول\nفيما يجب على المكري\nالفصل 742\nنص\nالقسم الثاني\nالفصل 796\nنص"
    rows=parse_legal_code([text]).articles
    assert [r.legal_subdomain for r in rows]==["lease","lease"]
    assert rows[0].subsection.startswith("الجزء")
    assert rows[1].subsection is None

def test_article_and_body_are_not_headings():
    parser=LegalStructureParser()
    assert not parser.consume("الفصل 796")
    assert not parser.consume("796 الفصل")
    assert not parser.consume("أما ما لم يكن لازما للخدمة فيجري عليه حكم الوديعة")
    assert parser.consume("فصل أول")  # structural ordinal, not numeric article

@pytest.mark.parametrize("question,text,section",[
    (QUESTION,"Non-paiement du loyer et résiliation du bail.","Bail"),
    ("loyers impayés et résiliation du bail","عدم دفع معين الكراء يترتب عنه الفسخ.","في الكراء"),
    ("عدم دفع معين الكراء وفسخ الكراء","Loyer impayé et résiliation du bail.","Bail / location"),
])
def test_lease_wins_over_sale_and_generic_contract(question,text,section):
    engine=create_engine("sqlite://");Base.metadata.create_all(engine);tenant=uuid4()
    with Session(engine) as session:
        a=chunk(tenant,"TEST COC",text);a.article_number="TEST-A"
        a.metadata_json={"legal_domain":"contract_law","legal_subdomain":"lease","section":section}
        b=chunk(tenant,"TEST COC","Contrat, propriétaire, prix, résolution du contrat de vente.")
        b.metadata_json={"legal_domain":"contract_law","legal_subdomain":"sale","section":"البيع"}
        c=chunk(tenant,"TEST GENERAL CONTRACT","Contrat et dommages-intérêts pour défaut de paiement.")
        c.metadata_json={"legal_domain":"contract_law","legal_subdomain":"general_obligations"}
        session.add_all([a,b,c]);session.commit()
        debug={};settings=Settings(_env_file=None,api_key="test-only-key-with-at-least-32-characters",tenant_id=tenant)
        result,_=retrieve(session,tenant,SearchRequest(question=question,top_k=10),settings,debug)
        assert [r.id for r in result]==[a.id]
        assert debug["query_understanding"]["subdomain"]=="lease"
        analyzer=LegalQueryAnalyzer().analyze(question);ranker=LegalDomainReranker()
        assert ranker.score(analyzer,a)["final_score"]>max(ranker.score(analyzer,x)["final_score"] for x in [b,c])+4
        assert "subdomain_candidates" in next(x for x in debug["candidate_documents"] if x["selected"])["channels"]

def test_empty_results_and_configurable_weights():
    analyzer=LegalQueryAnalyzer().analyze(QUESTION)
    a=chunk(uuid4(),"TEST COC","loyer impayé et résiliation du bail")
    a.metadata_json={"legal_domain":"contract_law","legal_subdomain":"lease","section":"في الكراء"}
    normal=LegalDomainReranker().score(analyzer,a)
    altered=LegalDomainReranker(RetrievalConfig(subdomain=0,section=0)).score(analyzer,a)
    assert normal["final_score"]-altered["final_score"]==4
    engine=create_engine("sqlite://");Base.metadata.create_all(engine)
    with Session(engine) as session:
        result,warnings=retrieve(session,a.tenant_id,SearchRequest(question=QUESTION),Settings(_env_file=None,api_key="test-only-key-with-at-least-32-characters",tenant_id=a.tenant_id))
        assert result==[] and any("NO_RELIABLE_SOURCE" in w for w in warnings)

def test_rebuild_preserves_sources_vectors_and_is_idempotent(client):
    from sqlalchemy import select
    from app.main import app
    from app.core.database import get_session
    from app.models.legal import LegalArticle
    from app.models.knowledge import SearchChunk
    from app.rebuild_legal_metadata import rebuild
    from app.services.retrieval.indexing import sync_legal_index
    from test_pdf import make_pdf
    api,settings=client
    doc=upload(api,make_pdf("TEST LAW\nDu bail\nArticle 1. Le loyer est payable."),{"title_fr":"TEST COC"}).json()["id"]
    assert api.post("/legal-documents/ingest",json={"document_id":doc}).status_code==200
    sessions=app.dependency_overrides[get_session]()
    session=next(sessions)
    try:
        sync_legal_index(session,settings.tenant_id);session.commit()
        article=session.scalar(select(LegalArticle))
        source=session.scalar(select(SearchChunk));source.embedding=[.1,.2];source.embedding_model="TEST"
        article.legal_subdomain=None;article.structure_version=None;session.commit()
        before=(article.id,article.original_text,article.checksum,article.start_offset,article.end_offset,article.page_start,source.id,source.embedding)
        for _ in range(2):
            result=rebuild(session,settings.tenant_id);session.commit()
            assert result["updated"]==1 and result["unmatched"]==0
            assert article.legal_subdomain=="lease"
            assert source.metadata_json["legal_subdomain"]=="lease"
            assert before==(article.id,article.original_text,article.checksum,article.start_offset,article.end_offset,article.page_start,source.id,source.embedding)
        assert rebuild(session,uuid4())["updated"]==0
    finally:
        sessions.close()
