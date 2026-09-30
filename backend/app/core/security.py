import secrets
from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader
from app.core.config import get_settings, Settings

header = APIKeyHeader(name="X-API-Key", auto_error=False)

def authenticate(key: str | None = Depends(header), settings: Settings = Depends(get_settings)):
    if not key or not secrets.compare_digest(key, settings.api_key.get_secret_value()):
        raise HTTPException(401, "Authentification requise")
    # Phase 1-4: one configured cabinet. Tenant identity never comes from a client header.
    return settings.tenant_id
