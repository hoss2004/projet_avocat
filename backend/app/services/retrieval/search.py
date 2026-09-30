from collections import defaultdict
import re
from sqlalchemy import select, or_, func, bindparam, Float, case
from app.models.knowledge import SearchChunk, Case, Vector
from app.schemas.assistant import Source
from app.services.retrieval.lexicon import query_tokens
from app.services.embeddings.providers import embedding_provider, ProviderError
from app.services.reranking.providers import LexicalReranker
from app.utils.arabic import normalize_for_search

class ScopeError(ValueError):
    pass

def scoped_query(tenant_id, request):
    query = select(SearchChunk).where(SearchChunk.tenant_id == tenant_id)
    if request.scope == "LEGAL_ONLY":
        query = query.where(SearchChunk.source_type == "law", SearchChunk.case_id.is_(None))
    elif request.scope == "CASE_ONLY":
        query = query.where(SearchChunk.source_type == "case_document", SearchChunk.case_id == request.case_id)
    else:
        query = query.where(or_(SearchChunk.source_type == "law", (SearchChunk.source_type == "case_document") & (SearchChunk.case_id == request.case_id)))
    if request.document_id:
        query = query.where(or_(SearchChunk.source_type == "case_document", SearchChunk.document_id == request.document_id))
    if request.at_date:
        query = query.where(or_(SearchChunk.source_type != "law", (SearchChunk.effective_from <= request.at_date) & or_(SearchChunk.effective_to.is_(None), SearchChunk.effective_to > request.at_date)))
    if request.legal_corpus!="all":
        is_case=SearchChunk.source_type=="case_document"
        corpus=SearchChunk.metadata_json["document_type"].as_string()
        query=query.where(or_(is_case,corpus=="jurisprudence" if request.legal_corpus=="jurisprudence" else or_(corpus!="jurisprudence",corpus.is_(None))))
    if request.document_type:
        query = query.where(SearchChunk.metadata_json["document_type"].as_string() == request.document_type)
    return query

def rrf(rankings):
    scores, chunks = defaultdict(float), {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking):
            scores[chunk.id] += 1 / (60 + rank + 1)
            chunks[chunk.id] = chunk
    return [(chunks[key], score) for key, score in scores.items()]

def retrieve(session, tenant_id, request, settings, debug=None):
    from app.services.retrieval.understanding import LegalQueryAnalyzer, CODES, source_identity
    from app.services.reranking.providers import LegalDomainReranker
    if request.case_id and not session.scalar(select(Case.id).where(Case.id==request.case_id,Case.tenant_id==tenant_id)):
        raise ScopeError("Dossier inaccessible")
    from app.services.retrieval.config import RetrievalConfig
    config=RetrievalConfig(**settings.retrieval_weights)
    minimum=max(request.min_relevance,settings.min_retrieval_score)
    analysis=LegalQueryAnalyzer().analyze(request.question,session,tenant_id)
    base=scoped_query(tenant_id,request)
    # Only an explicit article AND named code restrict the code, never classification alone.
    if analysis.exact_article and analysis.explicit_code:
        aliases=CODES[analysis.explicit_code][2]
        kind=SearchChunk.metadata_json["legal_text"]["kind"].as_string()
        base=base.where(or_(kind.is_(None),kind.in_(["main","code"])))
        base=base.where(or_(*(func.lower(SearchChunk.title).contains(a,autoescape=True) for a in aliases),SearchChunk.metadata_json["code_id"].as_string()==analysis.explicit_code))
    warnings=[]
    rankings=[]
    channels={}
    semantics={}
    def add(name,rows):
        rankings.append(rows)
        for row in rows: channels.setdefault(row.id,[]).append(name)
    exact=session.scalars(base.where(SearchChunk.article_number==analysis.exact_article).order_by(SearchChunk.id).limit(100)).all() if analysis.exact_article else []
    add("exact_reference",exact)
    def fts(question,name):
        tokens=query_tokens(question)
        if not tokens: return
        if session.bind.dialect.name=="postgresql":
            ts=func.to_tsquery("simple"," | ".join(tokens))
            vector=func.to_tsvector("simple",SearchChunk.search_text)
            rows=session.scalars(base.where(vector.op("@@")(ts)).order_by(func.ts_rank_cd(vector,ts).desc(),SearchChunk.id).limit(80)).all()
        else:
            conditions=[SearchChunk.search_text.contains(t,autoescape=True) for t in tokens]
            rows=session.scalars(base.where(or_(*conditions)).order_by(sum(case((c,1),else_=0) for c in conditions).desc(),SearchChunk.id).limit(80)).all()
        add(name,rows)
    fts(request.question,"fts_original")
    fts(analysis.query_ar,"fts_arabic")
    # Distinct legal concepts, not the number of occurrences of a generic word.
    conditions=[]
    for group in analysis.concept_groups:
        conditions.append(or_(*(SearchChunk.search_text.contains(normalize_for_search(t),autoescape=True) for t in group)))
    if conditions:
        coverage=sum(case((c,1),else_=0) for c in conditions)
        rows=session.scalars(base.where(or_(*conditions)).order_by(coverage.desc(),SearchChunk.id).limit(120)).all()
        add("legal_terms",rows)
        # Additional coherent candidates ensure a broad FTS list cannot crowd out the issue.
        rows=session.scalars(base.where(*conditions).order_by(SearchChunk.id).limit(120)).all()
        add("combined_legal_concepts",rows)
    # Dedicated section candidates are added to the global channels, never excluding other domains.
    if analysis.subdomain:
        focused=base.where(SearchChunk.metadata_json["legal_subdomain"].as_string()==analysis.subdomain)
        priority=sum(case((c,1),else_=0) for c in conditions) if conditions else SearchChunk.page_start
        add("subdomain_candidates",session.scalars(focused.order_by(priority.desc(),SearchChunk.id).limit(config.candidates)).all())
    provider=embedding_provider(settings)
    if provider and session.bind.dialect.name=="postgresql" and not exact:
        queries=list(dict.fromkeys([request.question]+([analysis.query_ar] if analysis.query_ar else [])))
        try:
            embeddings=provider.embed(queries)
            for index,embedding in enumerate(embeddings):
                filtered=base.where(SearchChunk.embedding_model==settings.embedding_provider+":"+settings.embedding_model,func.vector_dims(SearchChunk.embedding)==len(embedding)).cte("authorized_chunks").prefix_with("MATERIALIZED")
                distance=filtered.c.embedding.op("<=>",return_type=Float())(bindparam("query_vector",embedding,type_=Vector()))
                pairs=session.execute(select(filtered.c.id,distance.label("distance")).where(distance<0.65).order_by(distance,filtered.c.id).limit(80)).all()
                rows={c.id:c for c in session.scalars(base.where(SearchChunk.id.in_([i for i,d in pairs])))} if pairs else {}
                add("semantic_original" if index==0 else "semantic_arabic",[rows[i] for i,d in pairs])
                for ident,dist in pairs: semantics[ident]=max(semantics.get(ident,0),1-float(dist))
            if not semantics: warnings.append("Aucun résultat vectoriel suffisamment proche.")
        except ProviderError as exc:
            warnings.append(str(exc)+"; recherche textuelle utilisée.")
    elif not exact:
        warnings.append("Recherche sémantique inactive; recherche textuelle multilingue utilisée.")
    exact_ids={c.id for c in exact}
    reranker=LegalDomainReranker(config)
    scored=[]
    for chunk,fusion in rrf(rankings):
        score=reranker.score(analysis,chunk,semantics.get(chunk.id,0),fusion,chunk.id in exact_ids)
        scored.append((chunk,score))
    scored.sort(key=lambda item:(-item[1]["final_score"],str(item[0].id)))
    effective_threshold=max(minimum,scored[0][1]["final_score"]*config.relative_threshold) if scored and analysis.legal_issues and not exact else minimum
    selected=[];counts=defaultdict(int);seen=set();trace=[]
    for chunk,score in scored:
        duplicate=(chunk.document_id,normalize_for_search(chunk.original_text))
        if not score["evidence_sufficient"]: reason="insufficient_issue_evidence"
        elif score["final_score"]<effective_threshold: reason="below_relevance_threshold"
        elif duplicate in seen: reason="duplicate_passage"
        elif counts[chunk.document_id]>=config.per_document and chunk.id not in exact_ids: reason="source_diversity_limit"
        elif len(selected)>=request.top_k: reason="outside_top_k"
        else:
            reason="exact_reference" if chunk.id in exact_ids else "relevant_legal_concepts_and_domain" if analysis.domain!="unknown" else "relevant_text_or_semantics"
            selected.append((chunk,score["final_score"]))
            counts[chunk.document_id]+=1;seen.add(duplicate)
        trace.append({"source_id":str(chunk.id),"document_id":str(chunk.document_id),"title":chunk.title,"article_number":chunk.article_number,**score,"channels":channels[chunk.id],"selected":reason.startswith("relevant_") or reason=="exact_reference","reason_selected":reason})
    if request.at_date: warnings.append("Les textes sans date d'entrée en vigueur connue sont exclus du filtre temporel.")
    if debug is not None:
        debug.update({"query_understanding":analysis.to_dict(),"minimum_relevance":minimum,"effective_relevance_threshold":effective_threshold,"score_version":"legal_hierarchy_v2","retrieval_config":config.model_dump(),"scores_are_probabilities":False,"candidate_documents":trace})
    if not selected: warnings.append("NO_RELIABLE_SOURCE: Aucun passage suffisamment pertinent n’a été identifié.")
    sources=[]
    for chunk,score in selected:
        file_url=f"/legal-documents/{chunk.document_id}/file" if chunk.source_type=="law" else f"/cases/{chunk.case_id}/documents/{chunk.document_id}/file"
        domain,code=source_identity(chunk.title,chunk.metadata_json)
        sources.append(Source(id=chunk.id,source_type=chunk.source_type,document_id=chunk.document_id,article_id=chunk.article_id,case_id=chunk.case_id,title=chunk.title,article_number=chunk.article_number,original_text=chunk.original_text,language=chunk.language,page_start=chunk.page_start,page_end=chunk.page_end,metadata={**chunk.metadata_json,"domain":domain,"code_id":code},score=score,file_url=file_url))
    return sources,warnings
