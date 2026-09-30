"""Real PostgreSQL/pgvector integration, enabled with dedicated test credentials."""
import os
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.models.legal import LegalDocument
from app.core import database  # registers transaction-local tenant context

@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL integration URL not configured")
def test_postgres_rls_and_pgvector():
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    admin = create_engine(os.environ["TEST_ADMIN_DATABASE_URL"])
    tenant, other, document_id = uuid4(), uuid4(), uuid4()
    try:
        with Session(engine) as session:
            role = session.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
            assert role == (False, False)
            assert session.scalar(text("SELECT extname FROM pg_extension WHERE extname = 'vector'")) == "vector"
            assert session.scalar(text("SELECT '[1,0]'::vector <-> '[0,1]'::vector")) > 1.4
            session.info["tenant_id"] = tenant
            session.rollback()  # start a new transaction with tenant context
            session.add(LegalDocument(id=document_id, tenant_id=tenant, filename="TEST LAW.pdf", storage_key="TEST ONLY", checksum=uuid4().hex * 2))
            session.commit()
            assert session.scalar(text("SELECT count(*) FROM legal_documents WHERE id=:id"), {"id": document_id}) == 1
            session.rollback()
            session.info["tenant_id"] = other
            assert session.scalar(text("SELECT count(*) FROM legal_documents WHERE id=:id"), {"id": document_id}) == 0
            session.rollback()
            session.info.pop("tenant_id")
            assert session.scalar(text("SELECT count(*) FROM legal_documents WHERE id=:id"), {"id": document_id}) == 0
            session.rollback()
            session.info["tenant_id"] = other
            session.add(LegalDocument(tenant_id=tenant, filename="TEST LAW.pdf", storage_key="TEST ONLY", checksum=uuid4().hex * 2))
            from sqlalchemy.exc import DBAPIError
            with pytest.raises(DBAPIError):
                session.flush()
            session.rollback()
    finally:
        with admin.begin() as connection:
            connection.execute(text("DELETE FROM legal_documents WHERE id=:id"), {"id": document_id})
        engine.dispose()
        admin.dispose()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL integration URL not configured")
def test_hybrid_search_filters_before_vector_ranking(monkeypatch):
    from app.models.knowledge import SearchChunk, Case, CaseAnalysis, LegalTerm
    from app.services.retrieval.search import retrieve
    from app.schemas.assistant import SearchRequest
    from app.core.config import Settings
    owner,other,case_a,case_b=uuid4(),uuid4(),uuid4(),uuid4()
    engine=create_engine(os.environ["TEST_DATABASE_URL"])
    admin=create_engine(os.environ["TEST_ADMIN_DATABASE_URL"])
    settings=Settings(api_key="test-only-key-with-at-least-32-characters",tenant_id=owner,embedding_provider="ollama",embedding_model="TEST-VECTOR")
    class FakeEmbedding:
        def embed(self,texts): return [[1.0,0.0] for _ in texts]
    monkeypatch.setattr("app.services.retrieval.search.embedding_provider",lambda s:FakeEmbedding())
    try:
        with Session(engine) as session:
            session.info["tenant_id"]=owner
            session.add_all([Case(id=case_a,tenant_id=owner,title="TEST CASE A",reference="A"),Case(id=case_b,tenant_id=owner,title="TEST CASE B",reference="B")])
            session.flush()
            session.add(CaseAnalysis(tenant_id=owner,case_id=case_a,report={"TEST":"PRIVATE"}))
            session.add(LegalTerm(tenant_id=owner,key="TEST",domain="civil_procedure",terms_fr=["TEST"],terms_ar=[]))
            for case_id,content in [(case_a,"TEST ALPHA"),(case_b,"TEST SECRET")]:
                session.add(SearchChunk(tenant_id=owner,source_key=str(uuid4()),source_type="case_document",document_id=uuid4(),case_id=case_id,title="TEST LAW",original_text=content,search_text=content.lower(),language="en",page_start=1,page_end=1,embedding=[1.0,0.0],embedding_model="ollama:TEST-VECTOR",metadata_json={}))
            session.commit()
            results,warnings=retrieve(session,owner,SearchRequest(question="ALPHA",scope="CASE_ONLY",case_id=case_a),settings)
            assert len(results)==1
            assert results[0].original_text=="TEST ALPHA"
            assert "SECRET" not in str(results)
            # Verify database policy with raw SQL, independent of repository filtering.
            session.rollback()
            session.info["tenant_id"]=other
            assert session.scalar(text("SELECT count(*) FROM search_chunks WHERE tenant_id=:t"),{"t":owner})==0
            assert session.scalar(text("SELECT count(*) FROM case_analyses WHERE tenant_id=:t"),{"t":owner})==0
            assert session.scalar(text("SELECT count(*) FROM legal_terms WHERE tenant_id=:t"),{"t":owner})==0
    finally:
        with admin.begin() as connection:
            connection.execute(text("DELETE FROM search_chunks WHERE tenant_id=:t"),{"t":owner})
            connection.execute(text("DELETE FROM case_analyses WHERE tenant_id=:t"),{"t":owner})
            connection.execute(text("DELETE FROM legal_terms WHERE tenant_id=:t"),{"t":owner})
            connection.execute(text("DELETE FROM cases WHERE tenant_id=:t"),{"t":owner})
        engine.dispose();admin.dispose()
