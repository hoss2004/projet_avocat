"""Fail-closed source screening before generation; not legal applicability proof."""
from app.services.retrieval.understanding import LegalQueryAnalyzer
from app.services.reranking.providers import LegalDomainReranker
from app.services.retrieval.config import RetrievalConfig
from types import SimpleNamespace

class RetrievalQualityGate:
    def evaluate(self,question,sources,settings):
        analysis=LegalQueryAnalyzer().analyze(question)
        ranker=LegalDomainReranker(RetrievalConfig(**settings.retrieval_weights))
        accepted=[];checks=[]
        for source in sources:
            reason="accepted"
            decision="USE"
            identity=source.metadata.get("legal_text",{})
            if not source.original_text.strip():
                reason,decision="empty_source","REJECT"
            elif source.source_type=="law":
                chunk=SimpleNamespace(original_text=source.original_text,title=source.title,metadata_json=source.metadata,source_type=source.source_type)
                exact=bool(analysis.exact_article and source.article_number==analysis.exact_article)
                score=ranker.score(analysis,chunk,exact=exact)
                source_domain=score.get("document_domain", "unknown")
                if not score["evidence_sufficient"]:
                    reason,decision="insufficient_issue_evidence","REJECT"
                elif score["final_score"]<settings.min_retrieval_score:
                    reason,decision="insufficient_context_relevance","REJECT"
                elif (
                    analysis.domain != "unknown"
                    and source_domain not in {"unknown", analysis.domain}
                    and not exact
                ):
                    reason,decision="different_legal_domain","REJECT"
                elif (
                    analysis.domain != "unknown"
                    and source_domain == "unknown"
                    and score.get("concept_coverage", 0) < 0.5
                    and not exact
                ):
                    reason,decision="unknown_domain_with_weak_issue_coverage","REJECT"
                elif source.metadata.get("status") in {"repealed","replaced"}:
                    reason,decision="historical_text_requires_review","MAYBE"
                elif identity.get("kind")=="unidentified":
                    reason,decision="unidentified_composite_text","MAYBE"
            if decision!="REJECT": accepted.append(source)
            checks.append({
                "source_id":str(source.id), "accepted":decision!="REJECT",
                "source_relevance_decision":decision, "reason":reason,
            })
        return accepted,{"status":"passed" if accepted else "NO_RELIABLE_SOURCE","checks":checks,"legal_applicability":"requires_human_review"}
