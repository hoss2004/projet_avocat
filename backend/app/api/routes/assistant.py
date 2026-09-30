from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from app.api.routes.legal_documents import Repo, require
from app.core.config import get_settings
from app.models.knowledge import (
    CaseState,
    ConversationState,
    ConversationTurn,
    LegalDraft,
    LegalDraftVersion,
    SearchChunk,
)
from app.schemas.assistant import RagRequest, SearchRequest
from app.services.embeddings.providers import ProviderError
from app.services.exact_reference.router import QueryRouter
from app.services.exact_reference.service import ExactReferenceService
from app.services.legal_assistant.conversation import ConversationScopeError
from app.services.legal_assistant.intent import LegalIntentRouter
from app.services.rag.engine import query_exact_chat, query_rag
from app.services.retrieval.indexing import embed_pending, sync_legal_index
from app.services.retrieval.search import ScopeError, retrieve


router = APIRouter(tags=["Assistant juridique"])


@router.get("/assistant/status")
def status(repo: Repo, settings=Depends(get_settings)):
    total = repo.session.scalar(select(func.count()).select_from(SearchChunk).where(SearchChunk.tenant_id == repo.tenant_id))
    embedded = repo.session.scalar(select(func.count()).select_from(SearchChunk).where(
        SearchChunk.tenant_id == repo.tenant_id,
        SearchChunk.embedding_model == settings.embedding_provider + ":" + settings.embedding_model,
    ))
    models = []
    reachable = False
    if settings.llm_provider == "ollama" or settings.embedding_provider == "ollama":
        try:
            response = httpx.get(settings.ollama_url + "/api/tags", timeout=3)
            response.raise_for_status()
            models = [item["name"] for item in response.json().get("models", [])]
            reachable = True
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    return {
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "indexed_chunks": total,
        "embedded_chunks": embedded,
        "local_model_server_reachable": reachable,
        "available_models": models,
        "remote_transmission_allowed": settings.allow_remote_llm,
        "max_tool_calls": settings.max_tool_calls,
    }


@router.post("/search/legal")
def legal_search(body: SearchRequest, repo: Repo, settings=Depends(get_settings)):
    body = body.model_copy(update={"scope": "LEGAL_ONLY", "case_id": None})
    route = QueryRouter().route(body, repository=repo)
    if route.query_type in {"EXACT_REFERENCE_QUERY", "MULTI_EXACT_REFERENCE_QUERY"}:
        exact = ExactReferenceService().execute(repo, route.exact_reference, body)
        response = {
            "query_type": exact["query_type"], "status": exact["status"],
            "sources": exact["sources"], "warnings": [],
        }
        if "candidates" in exact:
            response["candidates"] = exact["candidates"]
        if body.debug:
            response["debug"] = exact["debug"]
        return response
    sync_legal_index(repo.session, repo.tenant_id)
    repo.session.commit()
    trace = {}
    sources, warnings = retrieve(repo.session, repo.tenant_id, body, settings, trace if body.debug else None)
    repo.session.commit()
    if body.debug:
        trace["query_type"] = "CONCEPTUAL_LEGAL_SEARCH"
    return {
        "query_type": "CONCEPTUAL_LEGAL_SEARCH", "sources": sources, "warnings": warnings,
        **({"debug": trace} if body.debug else {}),
    }


@router.post("/search/case")
def case_search(body: SearchRequest, repo: Repo, settings=Depends(get_settings)):
    if not body.case_id:
        raise HTTPException(422, "Dossier obligatoire")
    try:
        trace = {}
        sources, warnings = retrieve(
            repo.session, repo.tenant_id, body.model_copy(update={"scope": "CASE_ONLY"}),
            settings, trace if body.debug else None,
        )
        return {"sources": sources, "warnings": warnings, **({"debug": trace} if body.debug else {})}
    except ScopeError as exc:
        raise HTTPException(404, str(exc))


@router.post("/rag/query")
def rag_query(body: RagRequest, repo: Repo, settings=Depends(get_settings)):
    decision = LegalIntentRouter().route(body, repository=repo)
    if decision.user_request.primary_intent.value == "EXACT_REFERENCE_QUERY":
        try:
            return query_exact_chat(repo.session, repo.tenant_id, body, settings, decision)
        except ConversationScopeError as exc:
            raise HTTPException(404, str(exc))
    sync_legal_index(repo.session, repo.tenant_id)
    repo.session.commit()
    try:
        return query_rag(
            repo.session, repo.tenant_id, body, settings,
            legal_request=decision.user_request,
            exact_reference_query=decision.exact_reference_query,
        )
    except (ScopeError, ConversationScopeError) as exc:
        raise HTTPException(404, str(exc))


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: UUID, repo: Repo):
    state = require(repo.get(ConversationState, conversation_id))
    turns = repo.session.scalars(select(ConversationTurn).where(
        ConversationTurn.tenant_id == repo.tenant_id,
        ConversationTurn.conversation_id == conversation_id,
    ).order_by(ConversationTurn.ordinal)).all()
    return {
        "id": state.id,
        "current_case_id": state.current_case_id,
        "current_draft_id": state.current_draft_id,
        "current_focus": state.current_focus,
        "last_modified_section": state.last_modified_section,
        "pending_questions": state.pending_questions,
        "turns": turns,
    }


@router.get("/drafts/{draft_id}")
def draft(draft_id: UUID, repo: Repo):
    return require(repo.get(LegalDraft, draft_id))


@router.get("/drafts/{draft_id}/versions")
def draft_versions(draft_id: UUID, repo: Repo):
    require(repo.get(LegalDraft, draft_id))
    return repo.session.scalars(select(LegalDraftVersion).where(
        LegalDraftVersion.tenant_id == repo.tenant_id,
        LegalDraftVersion.draft_id == draft_id,
    ).order_by(LegalDraftVersion.version.desc())).all()


@router.get("/cases/{case_id}/state")
def case_state(case_id: UUID, repo: Repo):
    state = repo.session.scalar(select(CaseState).where(
        CaseState.tenant_id == repo.tenant_id,
        CaseState.case_id == case_id,
    ))
    return require(state)


@router.post("/search/index")
def index(repo: Repo, settings=Depends(get_settings), batch_size: int = Query(16, ge=1, le=64)):
    count = sync_legal_index(repo.session, repo.tenant_id)
    repo.session.commit()
    try:
        result = embed_pending(repo.session, repo.tenant_id, settings, batch_size)
        return {"new_chunks": count, **result}
    except ProviderError as exc:
        raise HTTPException(503, str(exc))


@router.get("/search/index/status")
def index_status(repo: Repo, settings=Depends(get_settings)):
    from collections import Counter
    from sqlalchemy.orm import load_only
    from app.models.legal import LegalArticle, LegalDocument

    documents = repo.session.scalars(select(LegalDocument).where(
        LegalDocument.tenant_id == repo.tenant_id
    ).options(load_only(
        LegalDocument.id, LegalDocument.title_ar, LegalDocument.title_fr,
        LegalDocument.filename, LegalDocument.language, LegalDocument.ingestion_status,
        LegalDocument.document_type,
    ))).all()
    articles = repo.session.execute(select(
        LegalArticle.document_id, LegalArticle.legal_domain,
        LegalArticle.legal_subdomain, LegalArticle.legal_text,
    ).where(LegalArticle.tenant_id == repo.tenant_id)).all()
    chunks = repo.session.execute(select(
        SearchChunk.document_id, func.count(),
        func.count(SearchChunk.embedding).filter(
            SearchChunk.embedding_model == settings.embedding_provider + ":" + settings.embedding_model
        ),
    ).where(
        SearchChunk.tenant_id == repo.tenant_id,
        SearchChunk.source_type == "law",
    ).group_by(SearchChunk.document_id)).all()
    counts = {row[0]: {"indexed": row[1], "embeddings": row[2]} for row in chunks}
    result = []
    for document in documents:
        rows = [article for article in articles if article.document_id == document.id]
        identities = {article.legal_text.get("id", "main"): article.legal_text for article in rows}
        result.append({
            "document_id": str(document.id),
            "title": document.title_ar or document.title_fr or document.filename,
            "language": document.language,
            "status": document.ingestion_status,
            "corpus": "jurisprudence" if document.document_type == "jurisprudence" else "tunisian_law",
            "articles": len(rows),
            **counts.get(document.id, {"indexed": 0, "embeddings": 0}),
            "domains": dict(Counter(article.legal_domain or "unknown" for article in rows)),
            "subdomains": dict(Counter(article.legal_subdomain or "unclassified" for article in rows)),
            "legal_texts": list(identities.values()),
            "unidentified_articles": sum(article.legal_text.get("kind") == "unidentified" for article in rows),
        })
    return {
        "documents_count": len(result),
        "articles_count": len(articles),
        "embeddings_count": sum(item["embeddings"] for item in result),
        "documents": result,
        "cabinet_memory": {"enabled": False, "searchable": False},
        "conversation_memory": {"enabled": True, "persistent": True},
        "case_documents": "isolated_by_tenant_and_case",
    }
