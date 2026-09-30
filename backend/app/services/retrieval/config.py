"""Single validated configuration for retrieval ranking and candidate budgets.
Environment example: RETRIEVAL_WEIGHTS='{"section": 1.5}' and MIN_RETRIEVAL_SCORE=0.75.
"""
from pydantic import BaseModel, ConfigDict, Field
class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semantic: float = Field(default=1,ge=0)
    lexical: float = Field(default=2,ge=0)
    domain: float = Field(default=2,ge=0)
    low_priority_penalty: float = Field(default=2.5,ge=0)
    other_domain_penalty: float = Field(default=.7,ge=0)
    subdomain: float = Field(default=2.5,ge=0)
    subdomain_penalty: float = Field(default=1.5,ge=0)
    section: float = Field(default=1.5,ge=0)
    preferred_code: float = Field(default=.5,ge=0)
    coherence: float = Field(default=1.5,ge=0)
    phrase: float = Field(default=.5,ge=0)
    phrase_cap: float = Field(default=1,ge=0)
    exact: float = Field(default=10,ge=0)
    fusion_cap: float = Field(default=.1,ge=0)
    semantic_evidence: float = Field(default=.5,ge=0,le=1)
    issue_coverage: float = Field(default=2/3,ge=0,le=1)
    relative_threshold: float = Field(default=.55,ge=0,le=1)
    candidates: int = Field(default=120,ge=1,le=1000)
    per_document: int = Field(default=3,ge=1,le=100)
