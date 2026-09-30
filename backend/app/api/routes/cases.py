from uuid import UUID
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from app.api.routes.legal_documents import Repo, require
from app.core.config import get_settings
from app.models.knowledge import Case, CaseDocument, CaseEvent
from app.schemas.assistant import CaseCreate, RagRequest
from app.services.ingestion.cases import import_case_pdf
from app.parsers.pdf_parser import PDFError
from app.services.rag.engine import query_rag
from app.services.retrieval.indexing import sync_legal_index

router=APIRouter(tags=["Dossiers"])

def document_out(document):
    return {"id":document.id,"case_id":document.case_id,"filename":document.filename,"language":document.language,"warnings":document.warnings,"page_count":len(document.pages),"document_type":document.document_type,"readable":any(p.strip() for p in document.pages),"created_at":document.created_at}

@router.post("/cases")
def create_case(body: CaseCreate, repo: Repo):
    case=Case(tenant_id=repo.tenant_id,**body.model_dump())
    repo.session.add(case)
    repo.session.commit()
    repo.session.refresh(case)
    return case

@router.get("/cases")
def list_cases(repo: Repo, offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=200)):
    return repo.session.scalars(select(Case).where(Case.tenant_id==repo.tenant_id).order_by(Case.created_at.desc()).offset(offset).limit(limit)).all()

@router.get("/cases/{case_id}")
def get_case(case_id:UUID,repo:Repo):
    return require(repo.get(Case,case_id))

@router.post("/cases/{case_id}/documents")
def upload_document(case_id:UUID,repo:Repo,file:UploadFile=File(...),settings=Depends(get_settings)):
    require(repo.get(Case,case_id))
    try:
        data=file.file.read(settings.max_upload_size+1)
        if len(data)>settings.max_upload_size:
            raise HTTPException(413,"PDF trop volumineux")
        document=import_case_pdf(repo.session,repo.tenant_id,case_id,data,file.filename or "piece.pdf",settings)
        from app.services.case_jobs import latest,enqueue
        previous=latest(repo.session,repo.tenant_id,case_id)
        if previous and str(document.id) not in {x["id"] for x in previous.snapshot}:
            enqueue(repo.session,repo.tenant_id,case_id)
        return document_out(document)
    except PDFError as exc:
        raise HTTPException(422,str(exc))
    finally:
        file.file.close()

@router.get("/cases/{case_id}/documents")
def list_documents(case_id:UUID,repo:Repo,offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=200)):
    require(repo.get(Case,case_id))
    values=repo.session.scalars(select(CaseDocument).where(CaseDocument.tenant_id==repo.tenant_id,CaseDocument.case_id==case_id).order_by(CaseDocument.created_at).offset(offset).limit(limit)).all()
    return [document_out(d) for d in values]

@router.get("/cases/{case_id}/documents/{document_id}/file")
def download_document(case_id:UUID,document_id:UUID,repo:Repo,settings=Depends(get_settings)):
    require(repo.get(Case,case_id))
    doc=require(repo.session.scalar(select(CaseDocument).where(CaseDocument.tenant_id==repo.tenant_id,CaseDocument.case_id==case_id,CaseDocument.id==document_id)))
    return FileResponse(settings.storage_path/doc.storage_key,media_type="application/pdf" if doc.storage_key.endswith(".pdf") else "text/plain; charset=utf-8" if doc.storage_key.endswith(".txt") else "message/rfc822",filename=doc.filename)

@router.get("/cases/{case_id}/timeline")
def timeline(case_id:UUID,repo:Repo,offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=200)):
    require(repo.get(Case,case_id))
    return repo.session.scalars(select(CaseEvent).where(CaseEvent.tenant_id==repo.tenant_id,CaseEvent.case_id==case_id).order_by(CaseEvent.event_date,CaseEvent.id).offset(offset).limit(limit)).all()

@router.post("/cases/{case_id}/ask")
def ask(case_id:UUID,body:RagRequest,repo:Repo,settings=Depends(get_settings)):
    require(repo.get(Case,case_id))
    body=body.model_copy(update={"case_id":case_id,"scope":"LEGAL_AND_CASE"})
    sync_legal_index(repo.session,repo.tenant_id)
    repo.session.commit()
    return query_rag(repo.session,repo.tenant_id,body,settings)
