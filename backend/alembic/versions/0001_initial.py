"""Immutable initial schema snapshot, pgvector extension and tenant policies."""
from alembic import op
from app.models.migration_v1 import define_schema
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    metadata = define_schema()
    metadata.create_all(op.get_bind(), checkfirst=False)
    for table in ("legal_documents", "legal_versions", "legal_articles"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO legal_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)")

def downgrade():
    define_schema().drop_all(op.get_bind(), checkfirst=False)
