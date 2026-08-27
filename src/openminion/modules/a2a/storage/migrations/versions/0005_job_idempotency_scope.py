"""Persist the exact idempotency scope reserved for an async A2A job."""

from __future__ import annotations


revision = "0005_job_idempotency_scope"
down_revision = "0004_job_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("ALTER TABLE jobs ADD COLUMN idempotency_scope TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    from alembic import op

    op.drop_column("jobs", "idempotency_scope")
