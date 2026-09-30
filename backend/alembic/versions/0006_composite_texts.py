"""Record per-article legal instrument identity in composite PDFs."""
from alembic import op
import sqlalchemy as sa
revision="0006"
down_revision="0005"
branch_labels=None
depends_on=None
def upgrade():
    op.add_column("legal_articles",sa.Column("legal_text",sa.JSON(),nullable=False,server_default=sa.text("'{}'")))
def downgrade():
    op.drop_column("legal_articles","legal_text")
