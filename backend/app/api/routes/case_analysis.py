from uuid import UUID
from typing import Literal
from pydantic import BaseModel
from fastapi import APIRouter,HTTPException,Depends
from fastapi.responses import Response
from sqlalchemy import select
from app.api.routes.legal_documents import Repo,require
from app.models.knowledge import Case,CaseAnalysis,CaseDraft
from app.services.case_jobs import enqueue,latest,output
from app.services.case_analysis import snapshot_documents
from app.core.config import get_settings
from app.schemas.assistant import Source,RagRequest,GeneratedAnswer
from app.services.llm.providers import llm_provider
from app.services.citations.claims import ClaimSourceValidator
from app.services.citations.validator import CitationValidator
from app.services.embeddings.providers import ProviderError

router=APIRouter(tags=["Analyse complète des dossiers"])
DRAFTS={"petition":"Projet de requête","submissions":"Projet de conclusions","opponent_response":"Réponse aux conclusions adverses","brief":"Projet de mémoire","consultation":"Consultation juridique","formal_notice":"Mise en demeure","client_letter":"Courrier client","summary":"Résumé du dossier","internal_note":"Note interne","timeline":"Chronologie","exhibit_list":"Liste de pièces","PREPARE_HEARING":"Préparation d'audience"}

class DraftRequest(BaseModel):
    document_type: Literal["petition","submissions","opponent_response","brief","consultation","formal_notice","client_letter","summary","internal_note","timeline","exhibit_list","PREPARE_HEARING"]


def authorized_analysis(repo,case_id,analysis_id):
    require(repo.get(Case,case_id))
    return require(repo.session.scalar(select(CaseAnalysis).where(CaseAnalysis.tenant_id==repo.tenant_id,CaseAnalysis.case_id==case_id,CaseAnalysis.id==analysis_id)))


@router.post("/cases/{case_id}/analyze",status_code=202)
def analyze(case_id:UUID,repo:Repo):
    require(repo.get(Case,case_id));return output(repo.session,enqueue(repo.session,repo.tenant_id,case_id))


@router.get("/cases/{case_id}/analysis")
def current(case_id:UUID,repo:Repo,summary:bool=False):
    require(repo.get(Case,case_id));job=latest(repo.session,repo.tenant_id,case_id)
    result=output(repo.session,job) if job else None
    if result and summary: result["report"]={}
    return result


@router.get("/cases/{case_id}/analyses/{analysis_id}")
def get_analysis(case_id:UUID,analysis_id:UUID,repo:Repo):
    return output(repo.session,authorized_analysis(repo,case_id,analysis_id))


def markdown_report(report):
    lines=["# Analyse du dossier — PROVISOIRE", "À relire et valider par l’avocat.",""]
    for section in report["sections"]:
        lines.append("## "+section["title"])
        if not section["items"]: lines.append("Non établi dans les sources disponibles.")
        for item in section["items"]:
            text=item.get("text") or item.get("question") or item.get("filename") or item.get("title") or str({k:v for k,v in item.items() if k not in {"sources","retrieval_debug","understanding"}})
            lines.append("- "+text)
            for sid in item.get("sources",[]):
                source=report["source_catalog"].get(sid)
                if source: lines.append("  Source : "+source["title"]+", p. "+str(source["page_start"])+" ["+sid+"]")
        lines.append("")
    return "\n".join(lines)


@router.get("/cases/{case_id}/analyses/{analysis_id}/export")
def export(case_id:UUID,analysis_id:UUID,repo:Repo):
    job=authorized_analysis(repo,case_id,analysis_id)
    if not job.report: raise HTTPException(409,"Rapport en cours de préparation")
    return Response(markdown_report(job.report),media_type="text/markdown; charset=utf-8",headers={"Content-Disposition":"attachment; filename=analyse-dossier.md"})


@router.post("/cases/{case_id}/analyses/{analysis_id}/drafts")
def draft(case_id:UUID,analysis_id:UUID,body:DraftRequest,repo:Repo,settings=Depends(get_settings)):
    job=authorized_analysis(repo,case_id,analysis_id)
    if job.status not in {"completed","partial"} or not job.report: raise HTTPException(409,"Terminez d'abord l'analyse du dossier")
    if job.snapshot!=snapshot_documents(repo.session,repo.tenant_id,case_id): raise HTTPException(409,"De nouvelles pièces sont présentes. Actualisez l'analyse avant de rédiger.")
    catalog=job.report["source_catalog"]
    source_ids=list(dict.fromkeys(s for section in job.report["sections"] if section["key"] in {"legal_analysis","client_arguments","opponent_arguments","claims","disputed_facts","applicable_law"} for row in section["items"] for s in row.get("sources",[])))
    # Balanced context preserves both documentary facts and legal sources.
    groups=[[i for i in source_ids if catalog[i]["source_type"]==kind] for kind in ["case_document","law"]]
    selected=[]
    for index in range(max(map(len,groups),default=0)):
        for group in groups:
            if index<len(group): selected.append(group[index])
    sources=[];budget=16000
    for sid in selected:
        if budget<200: break
        source=Source.model_validate(catalog[sid]);excerpt=source.original_text[:min(2000,budget)];budget-=len(excerpt);sources.append(source.model_copy(update={"original_text":excerpt}))
    from app.services.retrieval.quality import RetrievalQualityGate
    issues=job.report.get("memory",{}).get("legal_issues",[])
    gate_question=" ".join(i["question"] for i in issues)[:4000]
    sources,gate=RetrievalQualityGate().evaluate(gate_question,sources,settings)
    title=DRAFTS[body.document_type]
    extra=" Traite chaque argument adverse séparément : position citée, réponse proposée, base légale, pièce, faiblesse." if body.document_type=="opponent_response" else " Résumé, points essentiels, questions d'audience, arguments, articles, pièces et dates à vérifier." if body.document_type=="PREPARE_HEARING" else ""
    request=RagRequest(question="Prépare un BROUILLON de "+title+" à partir des sources. Signale les champs inconnus. Ne calcule aucun délai non établi. "+extra,case_id=case_id,scope="LEGAL_AND_CASE",mode="DRAFT_PREPARATION")
    outlines={"timeline":{"timeline","contradictions","case_sources"},"exhibit_list":{"documents","evidence","missing_documents"},"summary":{"case_card","summary","subject","legal_issues","human_review"},"PREPARE_HEARING":{"summary","claims","client_arguments","opponent_arguments","counterarguments","responses","procedure","timeline","applicable_law","evidence","human_review"},"opponent_response":{"opponent_arguments","responses","counterarguments","applicable_law","evidence","human_review"}}
    outline_report={**job.report,"sections":[s for s in job.report["sections"] if s["key"] in outlines.get(body.document_type,{s["key"] for s in job.report["sections"]})]}
    claims=[];warnings=[];provider=llm_provider(settings) if body.document_type not in {"timeline","exhibit_list","summary"} else None
    if provider and sources:
        try:
            answer=GeneratedAnswer.model_validate_json(provider.generate(request,sources));ClaimSourceValidator().validate(answer,sources,provider);claims=[c.model_dump() for c in answer.claims]
        except (ProviderError,ValueError): warnings.append("Rédaction IA rejetée ou indisponible. Trame documentaire uniquement.")
    else: warnings.append("Aucune rédaction IA disponible. Trame documentaire uniquement.")
    content={"title":title,"status":"DRAFT_REQUIRES_LAWYER_REVIEW","mode":"generated" if claims else "documentary_outline","claims":claims,"warnings":warnings,"source_catalog":{str(s.id):s.model_dump(mode="json") for s in sources},"outline":markdown_report(outline_report) if not claims else None,"quality_gate":gate,"context_sources":len(sources),"available_sources":len(catalog),"sent_or_signed":False}
    row=CaseDraft(tenant_id=repo.tenant_id,case_id=case_id,analysis_id=analysis_id,document_type=body.document_type,content=content);repo.session.add(row);repo.session.commit();repo.session.refresh(row)
    return {"id":str(row.id),**content}


@router.get("/cases/{case_id}/drafts")
def drafts(case_id:UUID,repo:Repo):
    require(repo.get(Case,case_id))
    return repo.session.scalars(select(CaseDraft).where(CaseDraft.case_id==case_id,CaseDraft.tenant_id==repo.tenant_id).order_by(CaseDraft.created_at.desc()).limit(100)).all()


class FactReview(BaseModel):
    status: Literal["established","disputed","unverified"]


@router.post("/cases/{case_id}/facts/{fact_id}/review")
def review_fact(case_id:UUID,fact_id:str,body:FactReview,repo:Repo):
    from app.models.knowledge import CaseDocument
    from copy import deepcopy
    require(repo.get(Case,case_id))
    for document in repo.session.scalars(select(CaseDocument).where(CaseDocument.tenant_id==repo.tenant_id,CaseDocument.case_id==case_id)):
        knowledge=deepcopy(document.knowledge or {})
        for item in knowledge.get("items",[]):
            if item.get("id")==fact_id and item.get("kind")=="fact":
                item["status"]=body.status;item["human_reviewed"]=True
                document.knowledge=knowledge;repo.session.commit()
                return output(repo.session,enqueue(repo.session,repo.tenant_id,case_id))
    raise HTTPException(404,"Observation inaccessible")
