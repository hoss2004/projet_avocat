"""Durable single-cabinet worker; expired leases resume after a process restart."""
import time,logging
from datetime import datetime,timedelta,timezone
from sqlalchemy import select,or_,and_
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.database import get_engine
from app.models.knowledge import CaseAnalysis
from app.services.case_analysis import analyze_job


def run_once():
    settings=get_settings()
    with Session(get_engine()) as session:
        session.info["tenant_id"]=settings.tenant_id
        cutoff=datetime.now(timezone.utc)-timedelta(minutes=10)
        job=session.scalar(select(CaseAnalysis).where(CaseAnalysis.tenant_id==settings.tenant_id,or_(CaseAnalysis.status=="pending",and_(CaseAnalysis.status=="running",CaseAnalysis.updated_at<cutoff))).order_by(CaseAnalysis.created_at).with_for_update(skip_locked=True).limit(1))
        if job is None: return False
        job.status="running";job.updated_at=datetime.now(timezone.utc);ident=job.id;session.commit()
        try:
            analyze_job(session,job,settings)
            from app.services.case_jobs import enqueue
            from app.services.case_analysis import snapshot_documents
            if job.snapshot!=snapshot_documents(session,job.tenant_id,job.case_id):
                enqueue(session,job.tenant_id,job.case_id)
        except Exception:
            session.rollback()
            job=session.get(CaseAnalysis,ident)
            job.status="failed";job.error="Analyse interrompue. Les pièces et extractions déjà enregistrées sont conservées; vous pouvez relancer."
            job.updated_at=datetime.now(timezone.utc);session.commit()
            logging.exception("Case analysis job failed: %s",ident)
        return True


if __name__=="__main__":
    while True:
        try:
            if not run_once(): time.sleep(2)
        except Exception:
            logging.exception("Analysis worker unavailable")
            time.sleep(5)
