"""Run with python -m app.rebuild_legal_metadata; metadata only, tenant scoped.
Matches stored article offsets and numbers, retaining UUIDs, text, pages and vectors.
"""
import json
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.database import get_engine
from app.models.legal import LegalDocument, LegalArticle
from app.parsers.legal_code_parser import parse_legal_code
from app.services.retrieval.understanding import source_identity
from app.services.retrieval.indexing import sync_legal_index, STRUCTURE_FIELDS

def rebuild(session, tenant_id):
    if session.bind.dialect.name=="postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant, 0))"),{"tenant":str(tenant_id)})
    totals={"documents":0,"updated":0,"unmatched":0,"subdomains":{}}
    for document in session.scalars(select(LegalDocument).where(LegalDocument.tenant_id==tenant_id)):
        if not document.pages: continue
        parsed={(a.start_offset,a.article_number):a for a in parse_legal_code(document.pages).articles}
        totals["documents"]+=1
        domain,_=source_identity(" ".join(t for t in [document.title_ar,document.title_fr,document.filename] if t))
        for article in session.scalars(select(LegalArticle).where(LegalArticle.tenant_id==tenant_id,LegalArticle.document_id==document.id)):
            candidate=parsed.get((article.start_offset,article.article_number))
            if not candidate:
                totals["unmatched"]+=1
                continue
            for name in STRUCTURE_FIELDS: setattr(article,name,getattr(candidate,name))
            identity=article.legal_text or {}
            if not article.legal_domain: article.legal_domain=domain if identity.get("kind") in {None,"main"} else source_identity(identity.get("title") or "")[0]
            totals["updated"]+=1
            key=article.legal_subdomain or "unclassified"
            totals["subdomains"][key]=totals["subdomains"].get(key,0)+1
    session.flush()
    sync_legal_index(session,tenant_id)
    return totals

if __name__=="__main__":
    settings=get_settings()
    with Session(get_engine()) as session:
        session.info["tenant_id"]=settings.tenant_id
        result=rebuild(session,settings.tenant_id)
        session.commit()
    print(json.dumps(result,ensure_ascii=False))
