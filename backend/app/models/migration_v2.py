"""Search projections and case records; original legal records remain immutable."""
from datetime import date, datetime, timezone
from uuid import UUID, uuid4
import json
from sqlalchemy import JSON, Text, ForeignKeyConstraint, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

class Vector(UserDefinedType):
    cache_ok = True
    def get_col_spec(self, **kwargs):
        return "VECTOR"
    def bind_processor(self, dialect):
        return lambda value: json.dumps(value) if value is not None else None
    def result_processor(self, dialect, coltype):
        return lambda value: json.loads(value) if isinstance(value, str) else value

class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    title: Mapped[str]
    reference: Mapped[str]
    client_name: Mapped[str | None]
    court: Mapped[str | None]
    case_number: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="open")
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))

class CaseDocument(Base):
    __tablename__ = "case_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "case_id", "checksum"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    case_id: Mapped[UUID] = mapped_column(index=True)
    filename: Mapped[str]
    document_type: Mapped[str] = mapped_column(default="piece")
    storage_key: Mapped[str]
    checksum: Mapped[str]
    language: Mapped[str]
    pages: Mapped[list] = mapped_column(JSON)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))

class CaseEvent(Base):
    __tablename__ = "case_events"
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "source_document_id"], ["case_documents.tenant_id", "case_documents.id"]),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    case_id: Mapped[UUID] = mapped_column(index=True)
    event_date: Mapped[date]
    description: Mapped[str] = mapped_column(Text)
    source_document_id: Mapped[UUID]
    source_page: Mapped[int]
    verified: Mapped[bool] = mapped_column(default=False)

class SearchChunk(Base):
    __tablename__ = "search_chunks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_key"),
        CheckConstraint("(source_type = 'law' AND case_id IS NULL) OR (source_type = 'case_document' AND case_id IS NOT NULL)"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    source_key: Mapped[str]
    source_type: Mapped[str]
    document_id: Mapped[UUID] = mapped_column(index=True)
    article_id: Mapped[UUID | None]
    case_id: Mapped[UUID | None] = mapped_column(index=True)
    title: Mapped[str]
    article_number: Mapped[str | None] = mapped_column(index=True)
    original_text: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text)
    language: Mapped[str]
    page_start: Mapped[int]
    page_end: Mapped[int]
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    effective_from: Mapped[date | None]
    effective_to: Mapped[date | None]
    embedding: Mapped[list | None] = mapped_column(Vector().with_variant(JSON, "sqlite"))
    embedding_model: Mapped[str | None]

class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    action: Mapped[str]
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
