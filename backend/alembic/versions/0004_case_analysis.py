"""Persistent case memory, analysis queue and drafts."""
from alembic import op
import sqlalchemy as sa
revision="0004"
down_revision="0003"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("cases",sa.Column("opponent_name",sa.String(),nullable=True))
    op.add_column("case_documents",sa.Column("knowledge",sa.JSON(),nullable=False,server_default=sa.text("'{}'")))
    op.create_table("case_analyses",sa.Column("id",sa.Uuid(),primary_key=True),sa.Column("tenant_id",sa.Uuid(),nullable=False),sa.Column("case_id",sa.Uuid(),nullable=False),sa.Column("status",sa.String(),nullable=False),sa.Column("progress",sa.JSON(),nullable=False),sa.Column("snapshot",sa.JSON(),nullable=False),sa.Column("report",sa.JSON(),nullable=False),sa.Column("error",sa.String(),nullable=True),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),sa.ForeignKeyConstraint(["tenant_id","case_id"],["cases.tenant_id","cases.id"]))
    op.create_table("case_drafts",sa.Column("id",sa.Uuid(),primary_key=True),sa.Column("tenant_id",sa.Uuid(),nullable=False),sa.Column("case_id",sa.Uuid(),nullable=False),sa.Column("analysis_id",sa.Uuid(),nullable=False),sa.Column("document_type",sa.String(),nullable=False),sa.Column("content",sa.JSON(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.ForeignKeyConstraint(["tenant_id","case_id"],["cases.tenant_id","cases.id"]))
    for table in ["case_analyses","case_drafts"]:
        op.create_index("ix_"+table+"_tenant_id",table,["tenant_id"])
        op.create_index("ix_"+table+"_case_id",table,["case_id"])
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO legal_app")

def downgrade():
    op.drop_table("case_drafts")
    op.drop_table("case_analyses")
    op.drop_column("case_documents","knowledge")
    op.drop_column("cases","opponent_name")
