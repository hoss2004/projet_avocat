from uuid import UUID,uuid4
from sqlalchemy import select
from app.main import app
from app.core.database import get_session
from app.models.knowledge import CaseAnalysis,CaseDocument,Case
from app.services.case_analysis import analyze_job,validated_items,source_for
from test_ingestion_api import client
from test_pdf import make_pdf


def run_job(api,settings,case_id):
    response=api.post(f"/cases/{case_id}/analyze")
    assert response.status_code==202,response.text
    gen=app.dependency_overrides[get_session]()
    session=next(gen)
    try:
        job=session.get(CaseAnalysis,UUID(response.json()["id"]))
        report=analyze_job(session,job,settings)
    finally: gen.close()
    return response.json()["id"],report


def test_all_pages_persistent_memory_new_piece_and_source_isolation(client,monkeypatch):
    api,settings=client
    a=api.post("/cases",json={"title":"TEST CASE A","client_name":"TEST CLIENT","opponent_name":"TEST OPPONENT"}).json()["id"]
    b=api.post("/cases",json={"title":"TEST CASE B"}).json()["id"]
    first=api.post(f"/cases/{a}/documents",files={"file":("TEST contrat.pdf",make_pdf("TEST LAW Contrat. Signature du contrat 02/03/2025.","TEST ARTICLE Un tiers revendique la propriété du bien saisi."),"application/pdf")})
    assert first.status_code==200,first.text
    api.post(f"/cases/{b}/documents",files={"file":("TEST SECRET.txt",b"TEST SECRET OTHER CASE ALPHA","text/plain")})
    job,report=run_job(api,settings,a)
    assert len(report["sections"])==24
    assert report["coverage"]["chunks_read"]==report["coverage"]["chunks_total"]>0
    assert all(s["case_id"]==a for s in report["source_catalog"].values() if s["source_type"]=="case_document")
    assert "TEST SECRET" not in str(report)
    assert next(s for s in report["sections"] if s["key"]=="established_facts")["items"]==[]
    assert all(x["status"]!="established" for x in report["memory"]["items"])
    fact=next(x for x in report["memory"]["items"] if x["kind"]=="fact")
    assert api.post(f"/cases/{b}/facts/{fact['id']}/review",json={"status":"established"}).status_code==404
    assert api.post(f"/cases/{a}/facts/{fact['id']}/review",json={"status":"established"}).status_code==200
    # New evidence invalidates the snapshot, while old extraction is reused.
    api.post(f"/cases/{a}/documents",files={"file":("TEST requete.txt",b"TEST LAW Signature du contrat 12/03/2025. Nouvelle allegation.","text/plain")})
    assert api.post(f"/cases/{a}/analyses/{job}/drafts",json={"document_type":"submissions"}).status_code==409
    _,second=run_job(api,settings,a)
    assert second["changes"]["reused_documents"]==1
    assert next(s for s in second["sections"] if s["key"]=="established_facts")["items"]
    assert len(second["changes"]["new_documents"])==1
    assert next(s for s in second["sections"] if s["key"]=="contradictions")["items"]
    assert api.get(f"/cases/{b}/analyses/{job}").status_code==404
    settings.tenant_id=uuid4()
    assert api.get(f"/cases/{a}/analyses/{job}").status_code==404


def test_scan_is_preserved_and_full_analysis_reports_coverage(client):
    api,settings=client
    case_id=api.post("/cases",json={}).json()["id"]
    response=api.post(f"/cases/{case_id}/documents",files={"file":("TEST scan.pdf",make_pdf(""),"application/pdf")})
    assert response.status_code==200,response.text
    assert response.json()["readable"] is False
    job,report=run_job(api,settings,case_id)
    assert report["coverage"]["documents_total"]==1
    assert report["coverage"]["documents_readable"]==0
    assert next(s for s in report["sections"] if s["key"]=="missing_documents")["items"]
    assert api.get(f"/cases/{case_id}/analyses/{job}/export").status_code==200
    draft=api.post(f"/cases/{case_id}/analyses/{job}/drafts",json={"document_type":"PREPARE_HEARING"})
    assert draft.status_code==200,draft.text
    assert draft.json()["sent_or_signed"] is False
    assert draft.json()["mode"]=="documentary_outline"


def test_chat_command_enqueues_only_authorized_case(client):
    api,settings=client
    case_id=api.post("/cases",json={}).json()["id"]
    r=api.post("/rag/query",json={"question":"Analyse cette القضية","scope":"LEGAL_AND_CASE","case_id":case_id})
    assert r.status_code==200,r.text
    assert r.json()["mode"]=="case_analysis_queued"
    settings.tenant_id=uuid4()
    assert api.post("/rag/query",json={"question":"Analyse cette القضية","scope":"LEGAL_AND_CASE","case_id":case_id}).status_code==404
