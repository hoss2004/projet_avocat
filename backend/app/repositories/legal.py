from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.legal import LegalDocument, LegalArticle, LegalVersion, LegalDocumentAlias
from app.services.exact_reference.normalization import normalize_article_number

class LegalRepository:
    """All reads are scoped to the authenticated cabinet before execution."""
    def __init__(self, session: Session, tenant_id: UUID):
        self.session, self.tenant_id = session, tenant_id

    def get(self, model, object_id):
        return self.session.scalar(select(model).where(model.id == object_id, model.tenant_id == self.tenant_id))

    def document_by_checksum(self, checksum):
        return self.session.scalar(select(LegalDocument).where(LegalDocument.tenant_id == self.tenant_id, LegalDocument.checksum == checksum))

    def documents(self, offset=0, limit=50):
        return self.session.scalars(select(LegalDocument).where(LegalDocument.tenant_id == self.tenant_id).order_by(LegalDocument.created_at, LegalDocument.id).offset(offset).limit(limit)).all()

    def document_aliases(self):
        return self.session.scalars(select(LegalDocumentAlias).where(LegalDocumentAlias.tenant_id == self.tenant_id)).all()

    def version_for(self, document_id):
        return self.session.scalar(select(LegalVersion).where(LegalVersion.document_id == document_id, LegalVersion.tenant_id == self.tenant_id))

    def articles(self, document_id, article_number=None, offset=0, limit=50):
        query = select(LegalArticle).where(LegalArticle.tenant_id == self.tenant_id, LegalArticle.document_id == document_id)
        if article_number is not None:
            query = query.where(LegalArticle.article_number == article_number)
        return self.session.scalars(query.order_by(LegalArticle.chunk_index).offset(offset).limit(limit)).all()

    def exact_article_reference(self, normalized_reference, article_suffix=None, document_id=None, at_date=None, legal_corpus="all", document_type=None):
        normalized_target = normalize_article_number(normalized_reference)
        target = normalized_target.normalized_reference
        target_suffix = article_suffix if article_suffix is not None else normalized_target.suffix
        candidate_query = (
            select(LegalArticle.id, LegalArticle.article_number)
            .join(LegalDocument, LegalDocument.id == LegalArticle.document_id)
            .join(LegalVersion, LegalVersion.id == LegalArticle.version_id)
            .where(
                LegalArticle.tenant_id == self.tenant_id,
                LegalDocument.tenant_id == self.tenant_id,
                LegalVersion.tenant_id == self.tenant_id,
            )
        )
        if document_id is not None:
            candidate_query = candidate_query.where(LegalArticle.document_id == document_id)
        if legal_corpus == "jurisprudence":
            candidate_query = candidate_query.where(LegalDocument.document_type == "jurisprudence")
        elif legal_corpus == "tunisian_law":
            candidate_query = candidate_query.where(LegalDocument.document_type != "jurisprudence")
        if document_type:
            candidate_query = candidate_query.where(LegalDocument.document_type == document_type)
        if at_date is not None:
            candidate_query = candidate_query.where(
                LegalVersion.effective_from <= at_date,
                (LegalVersion.effective_to.is_(None)) | (LegalVersion.effective_to > at_date),
            )
        candidate_ids = [
            article_id
            for article_id, article_number in self.session.execute(candidate_query)
            if normalize_article_number(article_number).normalized_reference == target
            and normalize_article_number(article_number).suffix == target_suffix
        ]
        if not candidate_ids:
            return []
        query = (
            select(LegalArticle, LegalDocument, LegalVersion)
            .join(LegalDocument, LegalDocument.id == LegalArticle.document_id)
            .join(LegalVersion, LegalVersion.id == LegalArticle.version_id)
            .where(
                LegalArticle.tenant_id == self.tenant_id,
                LegalDocument.tenant_id == self.tenant_id,
                LegalVersion.tenant_id == self.tenant_id,
                LegalArticle.id.in_(candidate_ids),
            )
            .order_by(
                LegalDocument.id,
                LegalVersion.version.desc(),
                LegalArticle.chunk_index,
                LegalArticle.id,
            )
        )
        return list(self.session.execute(query))
