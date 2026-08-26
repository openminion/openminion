"""Persist the parent agent that owns an async A2A job handle."""

from __future__ import annotations


revision = "0004_job_owner"
down_revision = "0003_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("ALTER TABLE jobs ADD COLUMN owner_agent_id TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    from alembic import op

    op.drop_column("jobs", "owner_agent_id")
