from abc import ABC, abstractmethod
from app.services.retrieval.lexicon import query_tokens

class RerankerProvider(ABC):
    @abstractmethod
    def rank(self, question, candidates): ...

class LexicalReranker(RerankerProvider):
    """Transparent local fallback; not a neural relevance model."""
    def rank(self, question, candidates):
        tokens = query_tokens(question)
        return sorted(candidates, key=lambda item: (item[1] + sum(t in item[0].search_text for t in tokens) * 0.0005), reverse=True)


class LegalDomainReranker:
    """Explainable business ranking; scores are not legal probabilities."""
    def __init__(self, config=None):
        from app.services.retrieval.config import RetrievalConfig
        self.config=config or RetrievalConfig()

    def score(self, analysis, chunk, semantic_score=0.0, fusion_score=0.0, exact=False):
        from app.services.retrieval.understanding import norm, matches, source_identity
        from app.services.retrieval.lexicon import query_tokens
        cfg=self.config
        from app.parsers.legal_structure import classify_heading
        metadata=chunk.metadata_json or {}
        section=metadata.get("section_ar") or metadata.get("section_fr") or metadata.get("section") or ""
        subdomain=metadata.get("legal_subdomain") or classify_heading(section)[0]
        text=norm(chunk.original_text)
        groups=analysis.concept_groups
        tokens=[norm(t) for t in query_tokens(analysis.original_question)]
        lexical=sum(matches(text,t) for t in tokens)/max(1,len(tokens))
        coverage=sum(any(matches(text,t) for t in group) for group in groups)/max(1,len(groups))
        windows=[text[i:i+700] for i in range(0,len(text),350)] or [text]
        coherence=max((sum(any(matches(w,t) for t in group) for group in groups)/max(1,len(groups)) for w in windows),default=0)
        domain,code=source_identity(chunk.title,chunk.metadata_json)
        domain_score=0.0
        if chunk.source_type=="law" and analysis.domain!="unknown":
            if domain==analysis.domain: domain_score=cfg.domain
            elif domain in analysis.excluded_or_low_priority_domains: domain_score=-cfg.low_priority_penalty
            elif domain!="unknown": domain_score=-cfg.other_domain_penalty
        source_score=cfg.preferred_code if code in analysis.preferred_codes else 0.0
        reranker_score=cfg.coherence*coherence
        if "third_party_ownership_of_seized_property" in analysis.legal_issues:
            phrases=["ملكية المعقول","ادعى الغير","revendication de propriete","proprietaire du bien saisi"]
            reranker_score+=min(cfg.phrase_cap,sum(matches(text,p) for p in phrases)*cfg.phrase)
        lexical_score=cfg.lexical*max(lexical,coverage)
        evidence=exact or (coherence>=cfg.issue_coverage if analysis.legal_issues else lexical_score>0 or semantic_score>=cfg.semantic_evidence)
        exact_score=cfg.exact if exact else 0.0
        subdomain_score=0.0
        section_score=0.0
        if chunk.source_type=="law" and analysis.subdomain and subdomain:
            subdomain_score=cfg.subdomain if subdomain==analysis.subdomain else -cfg.subdomain_penalty
            if subdomain==analysis.subdomain and classify_heading(section)[0]==subdomain and analysis.preferred_sections and any(classify_heading(p)[0]==subdomain for p in analysis.preferred_sections): section_score=cfg.section
        final=cfg.semantic*semantic_score+subdomain_score+section_score+lexical_score+domain_score+source_score+reranker_score+min(fusion_score,cfg.fusion_cap)+exact_score
        return {"section":section,"document_subdomain":subdomain,"subdomain_score":subdomain_score,"section_score":section_score,"semantic_score":round(semantic_score,6),"lexical_score":round(lexical_score,6),"domain_score":domain_score,"source_score":source_score,"reranker_score":round(reranker_score,6),"fusion_score":round(fusion_score,6),"exact_match_score":exact_score,"final_score":round(final,6),"document_domain":domain,"code_id":code,"concept_coverage":coverage,"coherent_coverage":coherence,"evidence_sufficient":evidence}
