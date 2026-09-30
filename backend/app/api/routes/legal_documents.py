from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.database import get_session
from app.core.security import authenticate
from app.models.legal import LegalDocument, LegalArticle, LegalVersion
from app.schemas.legal import SourceMetadata, DocumentOut, ArticleOut, VersionOut, IngestRequest
from app.repositories.legal import LegalRepository
from app.services.ingestion.legal import LegalIngestion, IngestionError
from app.parsers.legal_code_parser import canonical_article_number

router = APIRouter(tags=["Bibliothèque juridique"])

def repository(session: Session = Depends(get_session), tenant_id=Depends(authenticate)):
    return LegalRepository(session, tenant_id)

Repo = Annotated[LegalRepository, Depends(repository)]

def require(value):
    if value is None:
        raise HTTPException(404, "Source introuvable")
    return value

@router.post("/legal-documents/upload", response_model=DocumentOut)
def upload(repo: Repo, file: UploadFile = File(...), metadata: str = Form(...), settings=Depends(get_settings)):
    try:
        source = SourceMetadata.model_validate_json(metadata)
    except ValidationError:
        raise HTTPException(422, "Métadonnées JSON invalides: vérifier les champs, le titre, les dates et la source")
    try:
        data = file.file.read(settings.max_upload_size + 1)
        if len(data) > settings.max_upload_size:
            raise HTTPException(413, "Fichier trop volumineux")
        document, duplicate = LegalIngestion(repo, settings).upload(data, file.filename or "document.pdf", source)
        return document
    except IngestionError as exc:
        raise HTTPException(422, str(exc))
    finally:
        file.file.close()

@router.post("/legal-documents/ingest", response_model=DocumentOut)
def ingest(body: IngestRequest, repo: Repo, settings=Depends(get_settings)):
    try:
        return require(LegalIngestion(repo, settings).ingest(body.document_id))
    except IngestionError as exc:
        raise HTTPException(409, str(exc))

@router.get("/legal-documents", response_model=list[DocumentOut])
def documents(repo: Repo, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    return repo.documents(offset, limit)

@router.get("/legal-documents/{document_id}")
def document_detail(document_id: UUID, repo: Repo):
    document = require(repo.get(LegalDocument, document_id))
    version = repo.version_for(document_id)
    output = VersionOut.model_validate(version)
    output.next_version_id = repo.session.scalar(select(LegalVersion.id).where(LegalVersion.tenant_id == repo.tenant_id, LegalVersion.previous_version_id == version.id))
    return {"document": DocumentOut.model_validate(document), "version": output}

@router.get("/legal-documents/{document_id}/articles", response_model=list[ArticleOut])
def document_articles(document_id: UUID, repo: Repo, article_number: str | None = Query(None, max_length=100), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    require(repo.get(LegalDocument, document_id))
    number = canonical_article_number(article_number) if article_number is not None else None
    return repo.articles(document_id, number, offset, limit)

@router.get("/legal-documents/{document_id}/pages/{page_number}")
def document_page(document_id: UUID, page_number: int, repo: Repo):
    document = require(repo.get(LegalDocument, document_id))
    if not 1 <= page_number <= len(document.pages):
        raise HTTPException(404, "Page indisponible")
    return {"document_id": document_id, "page_number": page_number, "original_text": document.pages[page_number - 1]}

@router.get("/legal-documents/{document_id}/file")
def document_file(document_id: UUID, repo: Repo, settings=Depends(get_settings)):
    document = require(repo.get(LegalDocument, document_id))
    return FileResponse(settings.storage_path / document.storage_key, media_type="application/pdf", filename=document.filename)

@router.get("/legal-articles/{article_id}", response_model=ArticleOut)
def article_detail(article_id: UUID, repo: Repo):
    return require(repo.get(LegalArticle, article_id))
