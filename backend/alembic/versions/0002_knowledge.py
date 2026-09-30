"""Case records, hybrid search projections and audit events."""
from alembic import op
from app.models.migration_v2 import Base
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

def upgrade():
    Base.metadata.create_all(op.get_bind(), checkfirst=False)
    for table in Base.metadata.sorted_tables:
        name = table.name
        op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {name} USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {name} TO legal_app")
    op.execute("CREATE INDEX ix_search_chunks_fts ON search_chunks USING gin(to_tsvector('simple', search_text))")

def downgrade():
    Base.metadata.drop_all(op.get_bind(), checkfirst=False)
