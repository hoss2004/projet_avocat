import json
from uuid import UUID, uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.main import app
from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.models.legal import Base
from test_pdf import make_pdf

KEY = "test-only-key-with-at-least-32-characters"

@pytest.fixture
def client(tmp_path):
    settings = Settings(api_key=KEY, tenant_id=uuid4(), database_url="sqlite://", storage_path=tmp_path, llm_provider="disabled", embedding_provider="disabled")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    def session():
        with Session(engine) as value:
            yield value
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = session
    with TestClient(app) as value:
        value.headers["X-API-Key"] = KEY
        yield value, settings
    app.dependency_overrides.clear()
    engine.dispose()

def upload(client, data=None, metadata=None):
    return client.post("/legal-documents/upload", files={"file": ("TEST LAW.pdf", data or make_pdf("TEST LAW\nArticle 1. Le texte est artificiel pour les tests.", "Article 403. TEST ARTICLE. Le droit est fictif."), "application/pdf")}, data={"metadata": json.dumps(metadata or {"title_fr": "TEST LAW - SYNTHETIC"})})

def test_upload_ingest_exact_lookup_and_idempotency(client):
    api, settings = client
    pdf = make_pdf("TEST LAW\nArticle 1. Le texte est artificiel pour les tests.", "Article 403. TEST ARTICLE. Le droit est fictif.")
    response = upload(api, pdf)
    assert response.status_code == 200, response.text
    document_id = response.json()["id"]
    assert upload(api, pdf).json()["id"] == document_id
    for _ in range(2):
        result = api.post("/legal-documents/ingest", json={"document_id": document_id})
        assert result.status_code == 200, result.text
        assert result.json()["ingestion_status"] == "needs_review"
    articles = api.get(f"/legal-documents/{document_id}/articles").json()
    assert len(articles) == 2
    result = api.get(f"/legal-documents/{document_id}/articles", params={"article_number": "الفصل ٤٠٣"}).json()
    assert len(result) == 1 and result[0]["article_number"] == "403"
    assert result[0]["page_start"] == 2
    assert result[0]["text_fr"] == result[0]["original_text"]
    assert api.get(f"/legal-articles/{result[0]['id']}").status_code == 200
    assert "TEST LAW" in api.get(f"/legal-documents/{document_id}/pages/1").json()["original_text"]
    assert api.get(f"/legal-documents/{document_id}/file").content.startswith(b"%PDF-")
    assert api.get(f"/legal-documents/{document_id}").json()["version"]["status"] == "unknown"

def test_authentication_and_tenant_boundaries(client):
    api, settings = client
    doc = upload(api).json()["id"]
    api.post("/legal-documents/ingest", json={"document_id": doc})
    article = api.get(f"/legal-documents/{doc}/articles").json()[0]["id"]
    api.headers["X-API-Key"] = "wrong"
    assert api.get("/legal-documents").status_code == 401
    api.headers["X-API-Key"] = KEY
    settings.tenant_id = uuid4()
    assert api.get("/legal-documents").json() == []
    for suffix in ["", "/file", "/articles", "/pages/1"]:
        assert api.get(f"/legal-documents/{doc}{suffix}").status_code == 404
    assert api.get(f"/legal-articles/{article}").status_code == 404
    assert api.post("/legal-documents/ingest", json={"document_id": doc}).status_code == 404

def test_invalid_inputs_and_scan_status(client):
    api, settings = client
    assert upload(api, b"not a PDF").status_code == 422
    assert upload(api, metadata={"official": True, "title_fr": "TEST LAW"}).status_code == 422
    settings.max_upload_size = 4
    assert upload(api).status_code == 413
    settings.max_upload_size = 26214400
    doc = upload(api, make_pdf("")).json()["id"]
    assert api.post("/legal-documents/ingest", json={"document_id": doc}).json()["ingestion_status"] == "ocr_required"

def test_version_chain_keeps_old_content(client):
    api, settings = client
    first = upload(api).json()["id"]
    original = api.get(f"/legal-documents/{first}").json()["version"]
    second = upload(api, make_pdf("TEST LAW\nArticle 1. Le texte fictif a une autre version."), {"title_fr":"TEST LAW v2", "previous_version_id":original["id"]}).json()["id"]
    newer = api.get(f"/legal-documents/{second}").json()["version"]
    assert newer["version"] == 2
    assert newer["series_id"] == original["series_id"]
    assert api.get(f"/legal-documents/{first}").json()["version"]["next_version_id"] == newer["id"]
    conflict = upload(api, make_pdf("TEST LAW - conflicting new version"), {"title_fr":"TEST LAW", "previous_version_id":original["id"]})
    assert conflict.status_code == 422


def test_body_limit_before_multipart_parsing(client):
    api, settings = client
    settings.max_upload_size = 32
    response = api.post("/legal-documents/upload", content=b"x" * 66000)
    assert response.status_code == 413


def test_tampered_original_is_not_ingested(client):
    api, settings = client
    doc = upload(api).json()["id"]
    path = settings.storage_path / str(settings.tenant_id) / f"{doc}.pdf"
    path.write_bytes(b"%PDF-modified")
    response = api.post("/legal-documents/ingest", json={"document_id":doc})
    assert response.status_code == 409
    assert api.get(f"/legal-documents/{doc}/articles").json() == []

def test_cannot_link_previous_version_from_another_tenant(client):
    api, settings = client
    doc = upload(api).json()["id"]
    version = api.get(f"/legal-documents/{doc}").json()["version"]["id"]
    settings.tenant_id = uuid4()
    response = upload(api, metadata={"title_fr":"TEST LAW", "previous_version_id":version})
    assert response.status_code == 422
    assert api.get("/legal-documents").json() == []
