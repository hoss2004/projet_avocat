"""Conversation focus fields used to resolve short references safely."""
from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversation_states", sa.Column("last_assistant_response_id", sa.Uuid(), nullable=True))
    op.add_column("conversation_states", sa.Column("last_discussed_issue", sa.String(length=500), nullable=True))
    op.create_index(
        "ix_conversation_states_last_assistant_response_id",
        "conversation_states", ["last_assistant_response_id"],
    )


def downgrade():
    op.drop_index("ix_conversation_states_last_assistant_response_id", table_name="conversation_states")
    op.drop_column("conversation_states", "last_discussed_issue")
    op.drop_column("conversation_states", "last_assistant_response_id")
