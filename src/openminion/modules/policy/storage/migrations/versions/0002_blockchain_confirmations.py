import sqlalchemy as sa
from alembic import op


revision = "0002_blockchain_confirmations"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("policy_grants", sa.Column("approval_id", sa.Text(), nullable=True))
    op.add_column(
        "policy_decisions", sa.Column("approval_id", sa.Text(), nullable=True)
    )
    op.add_column(
        "policy_decisions", sa.Column("invocation_hash", sa.Text(), nullable=True)
    )
    op.create_table(
        "policy_pending_confirmations",
        sa.Column("approval_id", sa.Text(), primary_key=True),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("invocation_hash", sa.Text(), nullable=False),
        sa.Column("invocation_id", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("preview_json", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("resolution_action", sa.Text(), nullable=True),
        sa.Column("grant_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("resolved_at", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_policy_pending_confirmation_lookup",
        "policy_pending_confirmations",
        ["subject_id", "tool", "method", "invocation_hash", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_policy_pending_confirmation_lookup",
        table_name="policy_pending_confirmations",
    )
    op.drop_table("policy_pending_confirmations")
    op.drop_column("policy_decisions", "invocation_hash")
    op.drop_column("policy_decisions", "approval_id")
    op.drop_column("policy_grants", "approval_id")
