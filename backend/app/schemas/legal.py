from datetime import date, datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

class SourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title_ar: str | None = Field(default=None, max_length=1000)
    title_fr: str | None = Field(default=None, max_length=1000)
    document_type: Literal["constitution", "code", "tax", "special_law", "decree", "decree_law", "order", "jort", "jurisprudence"] = "code"
    source_url: HttpUrl | None = None
    source_name: str | None = Field(default=None, max_length=1000)
    official: bool = False
    publication_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    modification_date: date | None = None
    repeal_date: date | None = None
    status: Literal["unknown", "in_force", "repealed", "replaced"] = "unknown"
    previous_version_id: UUID | None = None

    @model_validator(mode="after")
    def validate_metadata(self):
        if not (self.title_ar or self.title_fr):
            raise ValueError("Un titre arabe ou français est obligatoire")
        if self.official and not (self.source_url and self.source_name):
            raise ValueError("Une source officielle doit être documentée")
        if self.effective_to and self.effective_from and self.effective_to <= self.effective_from:
            raise ValueError("Intervalle de validité incorrect")
        return self

class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title_ar: str | None
    title_fr: str | None
    document_type: str
    filename: str
    checksum: str
    source_url: str | None
    source_name: str | None
    official: bool
    language: str
    ingestion_status: str
    warnings: list
    created_at: datetime

class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    series_id: UUID
    version: int
    publication_date: date | None
    effective_from: date | None
    effective_to: date | None
    modification_date: date | None
    repeal_date: date | None
    status: str
    previous_version_id: UUID | None
    next_version_id: UUID | None = None

class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    version_id: UUID
    article_number: str
    book: str | None
    title_section: str | None
    chapter: str | None
    section: str | None
    subsection: str | None = None
    legal_domain: str | None = None
    legal_subdomain: str | None = None
    section_ar: str | None = None
    section_fr: str | None = None
    structure_version: str | None = None
    legal_text: dict = Field(default_factory=dict)
    original_text: str
    text_ar: str | None
    text_fr: str | None
    translation_is_official: bool
    language: str
    page_start: int
    page_end: int
    start_offset: int
    end_offset: int
    chunk_index: int
    checksum: str

class IngestRequest(BaseModel):
    document_id: UUID
