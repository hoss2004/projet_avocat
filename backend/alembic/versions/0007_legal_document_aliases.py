"""legal document aliases for exact references"""
from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "legal_document_aliases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("alias", sa.String(length=500), nullable=False),
        sa.Column("normalized_alias", sa.String(length=500), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column("alias_type", sa.String(length=40), nullable=False, server_default="alias"),
        sa.ForeignKeyConstraint(["tenant_id", "document_id"], ["legal_documents.tenant_id", "legal_documents.id"]),
        sa.UniqueConstraint("tenant_id", "document_id", "normalized_alias"),
    )
    op.create_index("ix_legal_document_aliases_tenant_id", "legal_document_aliases", ["tenant_id"])
    op.create_index("ix_legal_document_aliases_document_id", "legal_document_aliases", ["document_id"])
    op.create_index("ix_legal_document_aliases_normalized_alias", "legal_document_aliases", ["normalized_alias"])
    op.execute("ALTER TABLE legal_document_aliases ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE legal_document_aliases FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY legal_document_aliases_tenant ON legal_document_aliases USING (tenant_id = current_setting('app.tenant_id', true)::uuid)")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON legal_document_aliases TO legal_app")


def downgrade():
    op.drop_table("legal_document_aliases")
