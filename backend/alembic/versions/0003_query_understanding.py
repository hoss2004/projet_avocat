"""Add tenant legal vocabulary and a separate normalized search projection."""
from alembic import op
import sqlalchemy as sa
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("search_chunks", sa.Column("normalized_text", sa.Text(), nullable=True))
    op.create_table("legal_terms", sa.Column("id",sa.Uuid(),primary_key=True),sa.Column("tenant_id",sa.Uuid(),nullable=False),sa.Column("key",sa.String(),nullable=False),sa.Column("domain",sa.String(),nullable=False),sa.Column("terms_fr",sa.JSON(),nullable=False),sa.Column("terms_ar",sa.JSON(),nullable=False),sa.UniqueConstraint("tenant_id","key"))
    op.create_index("ix_legal_terms_tenant_id","legal_terms",["tenant_id"])
    op.execute("ALTER TABLE legal_terms ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE legal_terms FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON legal_terms USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)")
    op.execute("GRANT SELECT, INSERT, UPDATE ON legal_terms TO legal_app")


def downgrade():
    op.drop_table("legal_terms")
    op.drop_column("search_chunks","normalized_text")
