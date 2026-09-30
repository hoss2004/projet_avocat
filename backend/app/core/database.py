from functools import lru_cache
from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.security import authenticate

@lru_cache
def get_engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True)

def get_session(tenant_id=Depends(authenticate)):
    with Session(get_engine()) as session:
        session.info["tenant_id"] = tenant_id
        yield session

from sqlalchemy import event, text

@event.listens_for(Session, "after_begin")
def set_tenant_context(session, transaction, connection):
    if connection.dialect.name == "postgresql" and "tenant_id" in session.info:
        connection.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": str(session.info["tenant_id"])})
