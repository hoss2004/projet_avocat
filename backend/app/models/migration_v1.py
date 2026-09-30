from datetime import date, datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import String, Text, ForeignKey, UniqueConstraint, CheckConstraint, ForeignKeyConstraint, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class LegalDocument(Base):
    __tablename__ = "legal_documents"
    __table_args__ = (UniqueConstraint("tenant_id", "checksum"), UniqueConstraint("tenant_id", "id"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    country: Mapped[str] = mapped_column(String(2), default="TN")
    jurisdiction: Mapped[str] = mapped_column(default="Tunisia")
    document_type: Mapped[str] = mapped_column(default="code")
    title_ar: Mapped[str | None]
    title_fr: Mapped[str | None]
    filename: Mapped[str]
    storage_key: Mapped[str]
    checksum: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str | None]
    source_name: Mapped[str | None]
    official: Mapped[bool] = mapped_column(default=False)
    language: Mapped[str] = mapped_column(default="und")
    ingestion_status: Mapped[str] = mapped_column(default="uploaded")
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    original_text: Mapped[str | None] = mapped_column(Text)
    pages: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))

class LegalVersion(Base):
    __tablename__ = "legal_versions"
    __table_args__ = (
        UniqueConstraint("document_id"),
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "id", "document_id"),
        ForeignKeyConstraint(["tenant_id", "document_id"], ["legal_documents.tenant_id", "legal_documents.id"]),
        ForeignKeyConstraint(["tenant_id", "previous_version_id"], ["legal_versions.tenant_id", "legal_versions.id"]),
        UniqueConstraint("tenant_id", "series_id", "version"),
        UniqueConstraint("previous_version_id"),
        CheckConstraint("version > 0"),
        CheckConstraint("effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("legal_documents.id"))
    series_id: Mapped[UUID] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(default=1)
    publication_date: Mapped[date | None]
    effective_from: Mapped[date | None]
    effective_to: Mapped[date | None]
    modification_date: Mapped[date | None]
    repeal_date: Mapped[date | None]
    status: Mapped[str] = mapped_column(default="unknown")
    previous_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("legal_versions.id"))
    # next_version_id is derived from the successor's previous_version_id.

class LegalArticle(Base):
    __tablename__ = "legal_articles"
    __table_args__ = (
        UniqueConstraint("version_id", "chunk_index"),
        ForeignKeyConstraint(["tenant_id", "document_id"], ["legal_documents.tenant_id", "legal_documents.id"]),
        ForeignKeyConstraint(["tenant_id", "version_id", "document_id"], ["legal_versions.tenant_id", "legal_versions.id", "legal_versions.document_id"]),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("legal_documents.id"), index=True)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("legal_versions.id"), index=True)
    article_number: Mapped[str] = mapped_column(index=True)
    book: Mapped[str | None]
    title_section: Mapped[str | None]
    chapter: Mapped[str | None]
    section: Mapped[str | None]
    original_text: Mapped[str] = mapped_column(Text)
    text_ar: Mapped[str | None] = mapped_column(Text)
    text_fr: Mapped[str | None] = mapped_column(Text)
    translation_is_official: Mapped[bool] = mapped_column(default=False)
    normalized_text_for_search: Mapped[str] = mapped_column(Text)
    language: Mapped[str]
    page_start: Mapped[int]
    page_end: Mapped[int]
    start_offset: Mapped[int]
    end_offset: Mapped[int]
    chunk_index: Mapped[int]
    checksum: Mapped[str] = mapped_column(String(64))


def define_schema():
    return Base.metadata
