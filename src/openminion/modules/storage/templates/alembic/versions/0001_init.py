from alembic import op
import sqlalchemy as sa


revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_events",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_session_events_session_id_created",
        "session_events",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_session_events_session_id_created", table_name="session_events")
    op.drop_table("session_events")
