from datetime import datetime,timezone
from sqlalchemy import select,text
from app.models.knowledge import CaseAnalysis
from app.services.case_analysis import snapshot_documents


def enqueue(session,tenant,case_id):
    if session.bind.dialect.name=="postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),{"key":str(tenant)+":"+str(case_id)})
    active=session.scalar(select(CaseAnalysis).where(CaseAnalysis.tenant_id==tenant,CaseAnalysis.case_id==case_id,CaseAnalysis.status.in_(["pending","running"])).order_by(CaseAnalysis.created_at.desc()))
    if active: return active
    job=CaseAnalysis(tenant_id=tenant,case_id=case_id,snapshot=snapshot_documents(session,tenant,case_id),status="pending",progress={"stage":"En attente","done":0,"total":0},report={})
    session.add(job);session.commit();session.refresh(job)
    return job


def latest(session,tenant,case_id):
    return session.scalar(select(CaseAnalysis).where(CaseAnalysis.tenant_id==tenant,CaseAnalysis.case_id==case_id).order_by(CaseAnalysis.created_at.desc(),CaseAnalysis.id))


def output(session,job):
    return {"id":str(job.id),"case_id":str(job.case_id),"status":job.status,"progress":job.progress,"report":job.report,"error":job.error,"created_at":job.created_at,"updated_at":job.updated_at,"stale":job.snapshot!=snapshot_documents(session,job.tenant_id,job.case_id)}


def full_analysis_intent(question):
    from app.services.retrieval.understanding import norm
    q=norm(question)
    return any(t in q for t in ["analyse cette القضية","analyse cette قضية","analyse ce dossier","analyse complete","analyser entierement","حلل القضية","تحليل القضية"])
