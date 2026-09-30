"""Add source hierarchy classification without replacing articles or vectors."""
from alembic import op
import sqlalchemy as sa
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None
ARTICLE_FIELDS = ["subsection", "legal_domain", "legal_subdomain", "section_ar", "section_fr", "structure_version"]
TERM_FIELDS = ["subdomain", "term_fr", "term_ar"]
def upgrade():
    for name in ARTICLE_FIELDS: op.add_column("legal_articles",sa.Column(name,sa.String(),nullable=True))
    for name in TERM_FIELDS: op.add_column("legal_terms",sa.Column(name,sa.String(),nullable=True))
    for name in ["legal_domain","legal_subdomain"]: op.create_index("ix_legal_articles_"+name,"legal_articles",[name])
    op.execute("CREATE INDEX ix_search_chunks_legal_subdomain ON search_chunks (tenant_id, (metadata_json->>'legal_subdomain'))")
def downgrade():
    op.drop_index("ix_search_chunks_legal_subdomain",table_name="search_chunks")
    for name in ["legal_domain","legal_subdomain"]: op.drop_index("ix_legal_articles_"+name,table_name="legal_articles")
    for name in TERM_FIELDS: op.drop_column("legal_terms",name)
    for name in ARTICLE_FIELDS: op.drop_column("legal_articles",name)
