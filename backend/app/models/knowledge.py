"""Search projections and case records; original legal records remain immutable."""
from datetime import date, datetime, timezone
from uuid import UUID, uuid4
import json
from sqlalchemy import JSON, Text, ForeignKeyConstraint, UniqueConstraint, CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType
from app.models.legal import Base

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
    opponent_name: Mapped[str | None]
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
    knowledge: Mapped[dict] = mapped_column(JSON, default=dict)
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
    normalized_text: Mapped[str | None] = mapped_column(Text)
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


class LegalTerm(Base):
    __tablename__ = "legal_terms"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    key: Mapped[str]
    subdomain: Mapped[str | None]
    term_fr: Mapped[str | None]
    term_ar: Mapped[str | None]
    domain: Mapped[str]
    terms_fr: Mapped[list] = mapped_column(JSON)
    terms_ar: Mapped[list] = mapped_column(JSON)


class CaseAnalysis(Base):
    __tablename__ = "case_analyses"
    __table_args__ = (ForeignKeyConstraint(["tenant_id","case_id"],["cases.tenant_id","cases.id"]),)
    id: Mapped[UUID] = mapped_column(primary_key=True,default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    case_id: Mapped[UUID] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(default="pending")
    progress: Mapped[dict] = mapped_column(JSON,default=dict)
    snapshot: Mapped[list] = mapped_column(JSON,default=list)
    report: Mapped[dict] = mapped_column(JSON,default=dict)
    error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=lambda:datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(default=lambda:datetime.now(timezone.utc))


class CaseDraft(Base):
    __tablename__ = "case_drafts"
    __table_args__ = (ForeignKeyConstraint(["tenant_id","case_id"],["cases.tenant_id","cases.id"]),)
    id: Mapped[UUID] = mapped_column(primary_key=True,default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    case_id: Mapped[UUID] = mapped_column(index=True)
    analysis_id: Mapped[UUID]
    document_type: Mapped[str]
    content: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=lambda:datetime.now(timezone.utc))


class CaseState(Base):
    """Structured, durable memory for a client matter."""
    __tablename__ = "case_states"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"]),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    case_id: Mapped[UUID] = mapped_column(index=True)
    parties: Mapped[list] = mapped_column(JSON, default=list)
    roles: Mapped[dict] = mapped_column(JSON, default=dict)
    facts: Mapped[list] = mapped_column(JSON, default=list)
    allegations: Mapped[list] = mapped_column(JSON, default=list)
    disputed_facts: Mapped[list] = mapped_column(JSON, default=list)
    timeline: Mapped[list] = mapped_column(JSON, default=list)
    legal_issues: Mapped[list] = mapped_column(JSON, default=list)
    documents: Mapped[list] = mapped_column(JSON, default=list)
    verified_sources: Mapped[list] = mapped_column(JSON, default=list)
    open_questions: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class ConversationState(Base):
    """Small, selected memory used to resolve references across chat turns."""
    __tablename__ = "conversation_states"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    current_case_id: Mapped[UUID | None] = mapped_column(index=True)
    current_draft_id: Mapped[UUID | None] = mapped_column(index=True)
    recent_turns: Mapped[list] = mapped_column(JSON, default=list)
    current_focus: Mapped[str | None] = mapped_column(Text)
    last_modified_section: Mapped[str | None] = mapped_column(String(300))
    last_assistant_response_id: Mapped[UUID | None] = mapped_column(index=True)
    last_discussed_issue: Mapped[str | None] = mapped_column(String(500))
    pending_questions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint("tenant_id", "conversation_id", "ordinal"),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversation_states.tenant_id", "conversation_states.id"],
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    conversation_id: Mapped[UUID] = mapped_column(index=True)
    ordinal: Mapped[int]
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class LegalDraft(Base):
    """The active editable document; every mutation also creates a version."""
    __tablename__ = "legal_drafts"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), CheckConstraint("version > 0"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    case_id: Mapped[UUID | None] = mapped_column(index=True)
    conversation_id: Mapped[UUID | None] = mapped_column(index=True)
    document_type: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    sections: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(default=1)
    sources_used: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class LegalDraftVersion(Base):
    __tablename__ = "legal_draft_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "draft_id", "version"),
        ForeignKeyConstraint(["tenant_id", "draft_id"], ["legal_drafts.tenant_id", "legal_drafts.id"]),
        CheckConstraint("version > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    draft_id: Mapped[UUID] = mapped_column(index=True)
    version: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    sections: Mapped[list] = mapped_column(JSON, default=list)
    sources_used: Mapped[list] = mapped_column(JSON, default=list)
    operation: Mapped[str] = mapped_column(String(60), default="CREATE")
    instruction: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
