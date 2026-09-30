from datetime import date
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict, model_validator

class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=2, max_length=4000)
    debug: bool = False
    min_relevance: float = Field(default=0.75,ge=0,le=20)
    scope: Literal["LEGAL_ONLY", "CASE_ONLY", "LEGAL_AND_CASE"] = "LEGAL_ONLY"
    case_id: UUID | None = None
    document_id: UUID | None = None
    at_date: date | None = None
    language: Literal["fr", "ar", "en"] = "fr"
    legal_corpus: Literal["all","tunisian_law","jurisprudence"] = "all"
    document_type: str | None = Field(default=None, max_length=60)
    top_k: int = Field(default=8, ge=1, le=15)
    @model_validator(mode="after")
    def case_required(self):
        if self.scope != "LEGAL_ONLY" and self.case_id is None:
            raise ValueError("Sélectionner un dossier pour rechercher ses pièces")
        return self

class RagRequest(SearchRequest):
    mode: Literal[
        "QUICK_ANSWER", "LEGAL_RESEARCH", "CASE_ANALYSIS", "LEGAL_OPINION",
        "LEGAL_DRAFTING", "DOCUMENT_ANALYSIS", "DRAFT_PREPARATION",
        "CASE_TIMELINE", "COMPARE_ARGUMENTS", "ARGUMENTATION",
        "PROCEDURAL_ANALYSIS", "CASE_CHAT",
    ] = "LEGAL_RESEARCH"
    conversation_id: UUID | None = None
    previous_questions: list[str] = Field(default_factory=list, max_length=3)
    @model_validator(mode="after")
    def bounded_history(self):
        if any(len(q) > 1000 for q in self.previous_questions):
            raise ValueError("Historique trop long")
        return self

class Source(BaseModel):
    id: UUID
    source_type: str
    document_id: UUID
    article_id: UUID | None = None
    case_id: UUID | None = None
    title: str
    article_number: str | None = None
    original_text: str
    language: str
    page_start: int
    page_end: int
    metadata: dict
    score: float
    file_url: str

class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="Nouveau dossier",min_length=1,max_length=300)
    reference: str = Field(default_factory=lambda: "DOS-"+__import__("uuid").uuid4().hex[:8],min_length=1,max_length=100)
    opponent_name: str | None = Field(default=None,max_length=300)
    client_name: str | None = Field(default=None,max_length=300)
    court: str | None = Field(default=None,max_length=300)
    case_number: str | None = Field(default=None,max_length=100)

class Quote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    quote: str = Field(min_length=8,max_length=1500)

class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section: str = Field(max_length=100)
    text: str = Field(min_length=1,max_length=2500)
    grounding_type: Literal["SOURCE_BACKED", "GENERAL_REASONING", "USER_PROVIDED_FACT", "MISSING_INFORMATION"] = "SOURCE_BACKED"
    citations: list[Quote] = Field(default_factory=list,max_length=5)

class GeneratedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list[Claim] = Field(min_length=1,max_length=12)
