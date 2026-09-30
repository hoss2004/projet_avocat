from dataclasses import asdict, replace
from hashlib import sha256
import logging
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.models.legal import LegalDocument, LegalVersion, LegalArticle
from app.repositories.legal import LegalRepository
from app.parsers.pdf_parser import extract_pdf, PDFError, OCRRequired
from app.parsers.legal_code_parser import parse_legal_code
from app.services.language.detection import detect_language
from app.utils.arabic import normalize_for_search

logger = logging.getLogger("legal")

class IngestionError(ValueError):
    pass

class LegalIngestion:
    def __init__(self, repository: LegalRepository, settings):
        self.repo, self.settings = repository, settings

    def upload(self, data: bytes, filename: str, metadata):
        if len(data) > self.settings.max_upload_size:
            raise IngestionError("Fichier trop volumineux")
        if not data.startswith(b"%PDF-"):
            raise IngestionError("Signature PDF invalide")
        checksum = sha256(data).hexdigest()
        duplicate = self.repo.document_by_checksum(checksum)
        if duplicate:
            return duplicate, True
        previous = None
        if metadata.previous_version_id:
            previous = self.repo.get(LegalVersion, metadata.previous_version_id)
            if previous is None:
                raise IngestionError("Version précédente inaccessible")
        document_id = uuid4()
        key = f"{self.repo.tenant_id}/{document_id}.pdf"
        destination = self.settings.storage_path / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        document = LegalDocument(
            id=document_id, tenant_id=self.repo.tenant_id, filename=Path(filename.replace("\\", "/")).name[:255],
            storage_key=key, checksum=checksum, title_ar=metadata.title_ar, title_fr=metadata.title_fr,
            document_type=metadata.document_type, source_url=str(metadata.source_url) if metadata.source_url else None,
            source_name=metadata.source_name, official=metadata.official,
        )
        version = LegalVersion(
            tenant_id=self.repo.tenant_id, document_id=document_id,
            series_id=previous.series_id if previous else uuid4(), version=previous.version + 1 if previous else 1,
            previous_version_id=previous.id if previous else None,
            **metadata.model_dump(include={"publication_date", "effective_from", "effective_to", "modification_date", "repeal_date", "status"}),
        )
        try:
            with destination.open("xb") as handle:
                handle.write(data)
            self.repo.session.add(document)
            self.repo.session.flush()
            self.repo.session.add(version)
            self.repo.session.commit()
        except Exception as exc:
            self.repo.session.rollback()
            destination.unlink(missing_ok=True)
            if isinstance(exc, IntegrityError):
                duplicate = self.repo.document_by_checksum(checksum)
                if duplicate:
                    return duplicate, True
                raise IngestionError("Cette version possède déjà un successeur") from exc
            raise
        logger.info("document_uploaded")
        return document, False

    def ingest(self, document_id):
        session = self.repo.session
        document = session.scalar(select(LegalDocument).where(LegalDocument.id == document_id, LegalDocument.tenant_id == self.repo.tenant_id).with_for_update())
        if document is None:
            return None
        if document.ingestion_status in {"needs_review", "no_articles"}:
            return document
        data = (self.settings.storage_path / document.storage_key).read_bytes()
        if sha256(data).hexdigest() != document.checksum:
            raise IngestionError("Le fichier conservé ne correspond plus à son empreinte")
        try:
            extracted = extract_pdf(data, self.settings.max_pdf_pages)
        except PDFError as exc:
            document.ingestion_status = "ocr_required" if isinstance(exc, OCRRequired) else "failed"
            document.warnings = [str(exc)]
            session.commit()
            return document
        document.original_text = extracted.text
        document.pages = extracted.pages
        document.language = detect_language(extracted.text)
        parsed = parse_legal_code(extracted.pages)
        version = self.repo.version_for(document.id)
        from app.services.retrieval.understanding import source_identity
        domain,_=source_identity(" ".join(t for t in [document.title_ar,document.title_fr,document.filename] if t))
        for article in parsed.articles:
            identity=article.legal_text or {}
            inherited=domain if identity.get("kind") in {None,"main"} else source_identity(identity.get("title") or "")[0]
            if not article.legal_domain: article=replace(article,legal_domain=inherited)
            language = detect_language(article.original_text)
            session.add(LegalArticle(
                **asdict(article), tenant_id=self.repo.tenant_id, document_id=document.id, version_id=version.id,
                text_ar=article.original_text if language == "ar" else None,
                text_fr=article.original_text if language == "fr" else None,
                language=language, normalized_text_for_search=normalize_for_search(article.original_text),
                checksum=sha256(article.original_text.encode("utf-8")).hexdigest(),
            ))
        document.warnings = extracted.warnings + parsed.warnings
        document.ingestion_status = "needs_review" if parsed.articles else "no_articles"
        session.commit()
        logger.info("document_ingested")
        return document
