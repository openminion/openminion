"""Bind reserved A2A calls to their arguments."""

from __future__ import annotations

revision = "0006_call_request_fingerprint"
down_revision = "0005_job_idempotency_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op
    from sqlalchemy import inspect

    if not op.get_context().as_sql:
        columns = inspect(op.get_bind()).get_columns("idempotency_keys")
        if any(column["name"] == "request_fingerprint" for column in columns):
            return
    op.execute("ALTER TABLE idempotency_keys ADD COLUMN request_fingerprint TEXT")


def downgrade() -> None:
    from alembic import op

    op.drop_column("idempotency_keys", "request_fingerprint")
