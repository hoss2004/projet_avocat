"""Create local credentials without printing or overwriting them."""
from pathlib import Path
import secrets
from uuid import uuid4

root = Path(__file__).resolve().parents[1]
admin, application, api = (secrets.token_hex(32) for _ in range(3))
content = f"""POSTGRES_PASSWORD={admin}
APP_DB_PASSWORD={application}
DATABASE_URL=postgresql+psycopg://legal_app:{application}@postgres:5432/legal
MIGRATION_DATABASE_URL=postgresql+psycopg://legal:{admin}@postgres:5432/legal
API_KEY={api}
TENANT_ID={uuid4()}
STORAGE_PATH=/data/storage
MAX_UPLOAD_SIZE=26214400
MAX_PDF_PAGES=1500
"""
try:
    with (root / ".env").open("x", encoding="utf-8") as handle:
        handle.write(content)
except FileExistsError:
    raise SystemExit(".env existe déjà; aucune modification effectuée.")
print(".env créé. Les secrets ne sont pas affichés.")
