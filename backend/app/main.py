from fastapi import FastAPI
from app.api.routes.health import router as health_router
from app.core.logging import configure_logging
configure_logging()
app = FastAPI(title="LEGAL AI TUNISIA", version="0.1.0")
app.include_router(health_router)

from app.api.routes.legal_documents import router as legal_router
app.include_router(legal_router)

from app.core.upload_limit import UploadLimitMiddleware
app.add_middleware(UploadLimitMiddleware)

from app.api.routes.cases import router as cases_router
from app.api.routes.assistant import router as assistant_router
app.include_router(cases_router)
app.include_router(assistant_router)

from app.api.routes.case_analysis import router as analysis_router
app.include_router(analysis_router)
