from functools import lru_cache
from pathlib import Path
from uuid import UUID
from typing import Literal
from pydantic import SecretStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://legal@localhost/legal"
    migration_database_url: str | None = None
    api_key: SecretStr = Field(min_length=32)
    tenant_id: UUID
    storage_path: Path = Path("data/storage")
    max_upload_size: int = Field(default=26214400, gt=0)
    embedding_provider: Literal["disabled", "ollama"] = "disabled"
    embedding_model: str = "bge-m3"
    llm_provider: Literal["disabled", "ollama", "compatible"] = "disabled"
    llm_model: str = "qwen2.5:3b"
    llm_base_url: str = "http://localhost:8080/v1"
    llm_api_key: SecretStr = SecretStr("")
    allow_remote_llm: bool = False
    ollama_url: str = "http://ollama:11434"
    model_timeout: int = Field(default=180, ge=5, le=600)
    max_tool_calls: int = Field(default=8, ge=1, le=24)
    min_retrieval_score: float = Field(default=0.75,ge=0,le=100)
    retrieval_weights: dict[str, float] = Field(default_factory=dict)
    max_pdf_pages: int = Field(default=1500, gt=0)

@lru_cache
def get_settings() -> Settings:
    return Settings()
