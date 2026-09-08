import sqlalchemy as sa
from alembic import op


revision = "0002_pending_action_ownership"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pending_actions",
        sa.Column("agent_id", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "pending_actions",
        sa.Column("session_id", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("pending_actions", "session_id")
    op.drop_column("pending_actions", "agent_id")
