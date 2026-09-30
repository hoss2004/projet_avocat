from sqlalchemy import select, text
from sqlalchemy.orm import load_only
from app.models.legal import LegalArticle, LegalDocument, LegalVersion
from app.models.knowledge import SearchChunk
from app.utils.arabic import normalize_for_search
from app.services.embeddings.providers import embedding_provider

STRUCTURE_FIELDS = ["book","title_section","chapter","section","subsection","legal_domain","legal_subdomain","section_ar","section_fr","structure_version","legal_text"]

def article_metadata(article):
    return {name:getattr(article,name,None) for name in STRUCTURE_FIELDS}

def update_chunk_metadata(chunk, article):
    values=article_metadata(article)
    from app.services.retrieval.understanding import source_identity
    if all(chunk.metadata_json.get(k)==v for k,v in values.items()): return
    domain,code=source_identity(chunk.title,chunk.metadata_json)
    identity=article.legal_text or {}
    if identity.get("kind") not in {None,"main"}:
        title=identity.get("title") or "Texte juridique non identifié dans le PDF"
        domain,code=source_identity(title)
        chunk.title=title
    chunk.metadata_json={**chunk.metadata_json,**values,"domain":article.legal_domain or domain,"code_id":code,"corpus":"jurisprudence" if chunk.metadata_json.get("document_type")=="jurisprudence" else "tunisian_law"}
    chunk.search_text=normalize_for_search(chunk.title+" "+article.original_text+" "+" ".join(str(v) for v in values.values() if v))


def sync_legal_index(session, tenant_id, document_id=None):
    if session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant, 0))"), {"tenant":str(tenant_id)})
    from app.services.retrieval.understanding import seed_terms, source_identity
    seed_terms(session,tenant_id)
    for chunk in session.scalars(select(SearchChunk).where(SearchChunk.tenant_id==tenant_id,SearchChunk.normalized_text.is_(None))):
        chunk.normalized_text=normalize_for_search(chunk.original_text)
        domain,code=source_identity(chunk.title,chunk.metadata_json)
        chunk.metadata_json={**chunk.metadata_json,"domain":domain,"code_id":code}
    query = select(LegalArticle, LegalDocument, LegalVersion).join(LegalDocument, LegalArticle.document_id == LegalDocument.id).join(LegalVersion, LegalArticle.version_id == LegalVersion.id).where(LegalArticle.tenant_id == tenant_id, LegalDocument.tenant_id == tenant_id, LegalVersion.tenant_id == tenant_id)
    if document_id:
        query = query.where(LegalDocument.id == document_id)
    # Never fetch the full PDF pages once per article in a SQL join.
    query = query.options(load_only(LegalDocument.id, LegalDocument.title_ar, LegalDocument.title_fr, LegalDocument.filename, LegalDocument.document_type, LegalDocument.source_url, LegalDocument.official))
    existing = {c.source_key:c for c in session.scalars(select(SearchChunk).options(load_only(SearchChunk.id,SearchChunk.source_key,SearchChunk.title,SearchChunk.metadata_json)).where(SearchChunk.tenant_id == tenant_id, SearchChunk.source_type == "law"))}
    count = 0
    for article, document, version in session.execute(query):
        key = f"law:{article.id}"
        if key in existing:
            update_chunk_metadata(existing[key],article)
            continue
        title = document.title_ar or document.title_fr or document.filename
        identity=article.legal_text or {}
        if identity.get("kind") not in {None,"main"}: title=identity.get("title") or "Texte juridique non identifié dans le PDF"
        domain,code=source_identity(title if identity.get("kind") not in {None,"main"} else title+" "+(document.title_fr or "")+" "+document.filename)
        session.add(SearchChunk(tenant_id=tenant_id,source_key=key,source_type="law",document_id=document.id,article_id=article.id,case_id=None,title=title,article_number=article.article_number,original_text=article.original_text,normalized_text=normalize_for_search(article.original_text),search_text=normalize_for_search(title+" "+(document.title_fr or "")+" "+article.original_text+" "+" ".join(str(v) for v in article_metadata(article).values() if v)),language=article.language,page_start=article.page_start,page_end=article.page_end,effective_from=version.effective_from,effective_to=version.effective_to,metadata_json={"corpus":"jurisprudence" if document.document_type=="jurisprudence" else "tunisian_law",**article_metadata(article),"domain":article.legal_domain or domain,"code_id":code,"version":version.version,"status":version.status,"document_type":document.document_type,"source_url":document.source_url,"official":document.official,"review_required":True}))
        count += 1
    session.flush()
    return count

def embed_pending(session, tenant_id, settings, limit=16, case_id=None):
    provider = embedding_provider(settings)
    if provider is None:
        return {"indexed":0,"remaining":None,"message":"Embeddings désactivés"}
    model_id = settings.embedding_provider + ":" + settings.embedding_model
    query = select(SearchChunk).where(SearchChunk.tenant_id == tenant_id, (SearchChunk.embedding_model.is_(None)) | (SearchChunk.embedding_model != model_id)).order_by(SearchChunk.id)
    if case_id:
        query = query.where(SearchChunk.case_id == case_id)
    chunks = session.scalars(query.limit(limit)).all()
    if chunks:
        # Only indexing text is sent to the configured local provider, never to the LLM.
        vectors = provider.embed([chunk.original_text[:12000] for chunk in chunks])
        for chunk, vector in zip(chunks, vectors):
            chunk.embedding, chunk.embedding_model = vector, model_id
            chunk.metadata_json = {**chunk.metadata_json, "embedding_truncated":len(chunk.original_text)>12000, "embedding_token_truncation_allowed":True}
        session.commit()
    from sqlalchemy import func
    remaining = session.scalar(select(func.count()).select_from(query.subquery()))
    return {"indexed":len(chunks),"remaining":remaining,"model":model_id}
