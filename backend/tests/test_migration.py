from io import StringIO
from pathlib import Path
from uuid import uuid4
from alembic import command
from alembic.config import Config
from app.core.config import Settings

def test_postgres_migration_compiles_offline(monkeypatch):
    settings = Settings(api_key="test-only-key-with-at-least-32-characters", tenant_id=uuid4())
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    output = StringIO()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    for table in ["legal_documents", "legal_versions", "legal_articles"]:
        assert f"CREATE TABLE {table}" in sql
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql
    assert "TO legal_app" in sql
