from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LegalIntent(StrEnum):
    SIMPLE_CHAT = "SIMPLE_CHAT"
    NEW_CASE_FACT = "NEW_CASE_FACT"
    DRAFT_EDIT = "DRAFT_EDIT"
    EXACT_REFERENCE_QUERY = "EXACT_REFERENCE_QUERY"
    LEGAL_RESEARCH = "LEGAL_RESEARCH"
    CASE_ANALYSIS = "CASE_ANALYSIS"
    LEGAL_OPINION = "LEGAL_OPINION"
    LEGAL_DRAFTING = "LEGAL_DRAFTING"
    DOCUMENT_ANALYSIS = "DOCUMENT_ANALYSIS"
    ARGUMENTATION = "ARGUMENTATION"
    PROCEDURAL_ANALYSIS = "PROCEDURAL_ANALYSIS"
    CASE_CHAT = "CASE_CHAT"


class LegalToolName(StrEnum):
    SEARCH_LAW = "search_law"
    SEARCH_LEGAL_SOURCES = "search_legal_sources"
    LOOKUP_EXACT_REFERENCE = "lookup_exact_reference"
    SEARCH_CASE_LAW = "search_case_law"
    SEARCH_CASE_DOCUMENTS = "search_case_documents"
    READ_DOCUMENT = "read_document"
    SEARCH_CURRENT_CASE = "search_current_case"
    GET_ACTIVE_DRAFT = "get_active_draft"
    GET_CASE_STATE = "get_case_state"
    GET_CASE_CONTEXT = "get_case_context"
    UPDATE_CASE_STATE = "update_case_state"
    SAVE_DRAFT = "save_draft"
    SEARCH_VERIFIED_SOURCES = "search_verified_sources"
    SEARCH_UPLOADED_FILES = "search_uploaded_files"


class DraftOperation(StrEnum):
    ADD = "ADD"
    DELETE = "DELETE"
    REPLACE = "REPLACE"
    REWRITE = "REWRITE"
    EXPAND = "EXPAND"
    SHORTEN = "SHORTEN"
    MOVE = "MOVE"
    MERGE = "MERGE"
    CHANGE_TONE = "CHANGE_TONE"
    STRENGTHEN_ARGUMENT = "STRENGTHEN_ARGUMENT"
    SOFTEN_CONCLUSION = "SOFTEN_CONCLUSION"
    ADD_SOURCE = "ADD_SOURCE"
    REMOVE_ARGUMENT = "REMOVE_ARGUMENT"
    ADD_COUNTERARGUMENT = "ADD_COUNTERARGUMENT"
    UPDATE_WITH_NEW_FACTS = "UPDATE_WITH_NEW_FACTS"


class ConversationIntent(StrEnum):
    GENERAL_CHAT = "GENERAL_CHAT"
    LEGAL_QUESTION = "LEGAL_QUESTION"
    LEGAL_RESEARCH = "LEGAL_RESEARCH"
    LEGAL_OPINION = "LEGAL_OPINION"
    CASE_ANALYSIS = "CASE_ANALYSIS"
    CREATE_DRAFT = "CREATE_DRAFT"
    EDIT_DRAFT = "EDIT_DRAFT"
    CONTINUE_DRAFT = "CONTINUE_DRAFT"
    UPDATE_CASE_FACTS = "UPDATE_CASE_FACTS"
    ANALYZE_NEW_DOCUMENT = "ANALYZE_NEW_DOCUMENT"
    COMPARE_WITH_PREVIOUS_ANALYSIS = "COMPARE_WITH_PREVIOUS_ANALYSIS"
    NEW_CASE = "NEW_CASE"
    SWITCH_CASE = "SWITCH_CASE"
    CLARIFICATION = "CLARIFICATION"
    EXACT_REFERENCE_LOOKUP = "EXACT_REFERENCE_LOOKUP"


class CaseAction(StrEnum):
    NONE = "NONE"
    KEEP = "KEEP"
    CREATE = "CREATE"
    SWITCH = "SWITCH"
    UPDATE_FACTS = "UPDATE_FACTS"


class DraftAction(StrEnum):
    NONE = "NONE"
    CREATE = "CREATE"
    LOAD = "LOAD"
    EDIT = "EDIT"
    CONTINUE = "CONTINUE"


class ResponseMode(StrEnum):
    CHAT = "CHAT"
    LEGAL_ANALYSIS = "LEGAL_ANALYSIS"
    LEGAL_OPINION = "LEGAL_OPINION"
    DRAFT_EDIT = "DRAFT_EDIT"
    LEGAL_RESEARCH = "LEGAL_RESEARCH"
    EXACT_REFERENCE = "EXACT_REFERENCE"
    DOCUMENT_ANALYSIS = "DOCUMENT_ANALYSIS"


class ConversationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: ConversationIntent
    case_action: CaseAction
    draft_action: DraftAction
    retrieval_required: bool
    reasoning_required: bool
    response_mode: ResponseMode


class LegalToolCall(BaseModel):
    name: LegalToolName
    query: str | None = None
    reason: str
    issue_id: str | None = None


class ChatPlanningOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary_goal: LegalIntent
    secondary_goals: list[LegalIntent] = Field(default_factory=list, max_length=6)
    issue_queries: list[str] = Field(default_factory=list, max_length=8)
    requested_document_type: str | None = Field(default=None, max_length=80)
    requested_transformations: list[str] = Field(default_factory=list, max_length=8)
    mentioned_documents: list[str] = Field(default_factory=list, max_length=12)
    needs_legal_research: bool = True
    needs_case_law: bool = False
    needs_case_documents: bool = False
    needs_active_draft: bool = False
    needs_case_context: bool = False
    draft_operation: DraftOperation | None = None
    target_section: str | None = Field(default=None, max_length=300)
    new_case_facts: list[str] = Field(default_factory=list, max_length=12)
    create_new_case: bool = False
    case_title: str | None = Field(default=None, max_length=300)
    response_strategy: str = Field(min_length=1, max_length=800)


class UserTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary_goal: LegalIntent
    secondary_goals: list[LegalIntent] = Field(default_factory=list)
    explicit_references: list["ExplicitLegalReference"] = Field(default_factory=list)
    requested_document_type: str | None = None
    requested_transformations: list[str] = Field(default_factory=list)
    language: str
    case_id: UUID | None = None
    draft_id: UUID | None = None
    draft_operation: DraftOperation | None = None
    target_section: str | None = None
    new_case_facts: list[str] = Field(default_factory=list)
    create_new_case: bool = False
    case_title: str | None = None
    uploaded_documents: list[UUID] = Field(default_factory=list)
    mentioned_documents: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    issue_queries: list[str] = Field(default_factory=list)
    tool_calls: list[LegalToolCall] = Field(default_factory=list)
    response_strategy: str
    original_message: str


class ExplicitLegalReference(BaseModel):
    raw_text: str
    article_number: str
    article_suffix: str | None = None
    document_reference: str | None = None
    resolved_document_id: UUID | None = None


class LegalUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary_intent: LegalIntent
    secondary_goals: list[LegalIntent] = Field(default_factory=list)
    explicit_references: list[ExplicitLegalReference] = Field(default_factory=list)
    requested_output: str
    language: str
    case_id: UUID | None = None
    uploaded_documents: list[UUID] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    original_question: str


class EvidenceStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    NO_RELIABLE_SOURCE = "NO_RELIABLE_SOURCE"


class LegalIssue(BaseModel):
    id: str
    title: str
    description: str
    relevant_facts: list[str] = Field(default_factory=list)
    legal_questions: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    explicit_references: list[ExplicitLegalReference] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_status: EvidenceStatus = EvidenceStatus.NO_RELIABLE_SOURCE


class EvidenceSourceType(StrEnum):
    STATUTE = "STATUTE"
    REGULATION = "REGULATION"
    CASE_LAW = "CASE_LAW"
    CONSTITUTION = "CONSTITUTION"
    CONTRACT = "CONTRACT"
    COURT_DOCUMENT = "COURT_DOCUMENT"
    EXPERT_REPORT = "EXPERT_REPORT"
    CLIENT_DOCUMENT = "CLIENT_DOCUMENT"
    CORRESPONDENCE = "CORRESPONDENCE"
    ADMINISTRATIVE_DOCUMENT = "ADMINISTRATIVE_DOCUMENT"
    OTHER = "OTHER"


class LegalEvidence(BaseModel):
    evidence_id: str
    source_id: str
    source_type: EvidenceSourceType
    title: str
    original_text: str
    article_number: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    language: str | None = None
    document_id: str | None = None
    case_id: str | None = None
    issue_ids: list[str] = Field(default_factory=list)
    is_explicit_reference: bool = False
    hierarchy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CaseFact(BaseModel):
    id: str
    text: str
    status: str
    evidence_ids: list[str] = Field(default_factory=list)


class TimelineEvent(BaseModel):
    id: str
    date: str | None = None
    description: str
    evidence_ids: list[str] = Field(default_factory=list)


class LegalEvidencePack(BaseModel):
    case_facts: list[CaseFact] = Field(default_factory=list)
    disputed_facts: list[CaseFact] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    legal_issues: list[LegalIssue] = Field(default_factory=list)
    statutes: list[LegalEvidence] = Field(default_factory=list)
    case_law: list[LegalEvidence] = Field(default_factory=list)
    case_documents: list[LegalEvidence] = Field(default_factory=list)
    explicit_references: list[LegalEvidence] = Field(default_factory=list)
    other_evidence: list[LegalEvidence] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)

    def all_evidence(self) -> list[LegalEvidence]:
        unique: dict[str, LegalEvidence] = {}
        for item in self.statutes + self.case_law + self.case_documents + self.other_evidence:
            unique[item.evidence_id] = item
        return list(unique.values())


class ClaimSupportStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    NO_SOURCE = "NO_SOURCE"


class ClaimSupport(BaseModel):
    claim_index: int
    support_status: ClaimSupportStatus
    source_ids: list[str] = Field(default_factory=list)
    reason: str


class ConversationReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=6000)
    current_focus: str | None = Field(default=None, max_length=500)


class DraftSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=12000)


class DraftRevisionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=40000)
    sections: list[DraftSection] = Field(min_length=1, max_length=40)
    modified_section: str | None = Field(default=None, max_length=300)
    explanation: str = Field(min_length=1, max_length=1200)


class DraftEditOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_section: str = Field(min_length=1, max_length=300)
    replacement_title: str | None = Field(default=None, max_length=300)
    replacement_content: str | None = Field(default=None, max_length=12000)
    delete_target: bool = False
    full_rewrite: DraftRevisionOutput | None = None
    explanation: str = Field(min_length=1, max_length=1200)
