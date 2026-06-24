"""Modules a2a storage migrations versions 0002 audit records."""

from __future__ import annotations

from openminion.modules.storage.migrations.alembic import drop_sql_objects


revision = "0002_audit"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_records (
            id BIGSERIAL PRIMARY KEY,
            record_date DATE NOT NULL,
            ts TEXT NOT NULL,
            msg_id TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            from_agent TEXT NOT NULL,
            to_agent TEXT,
            to_capability TEXT,
            type TEXT NOT NULL,
            method TEXT NOT NULL,
            status TEXT NOT NULL,
            task_id TEXT,
            error_code TEXT,
            error_message TEXT,
            envelope_json TEXT,
            data_json TEXT
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_trace ON audit_records(trace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_records(ts)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_method ON audit_records(method)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_status ON audit_records(status)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_record_date ON audit_records(record_date)"
    )


def downgrade() -> None:
    drop_sql_objects(
        table_names=("audit_records",),
        index_names=(
            "idx_audit_trace",
            "idx_audit_ts",
            "idx_audit_method",
            "idx_audit_status",
            "idx_audit_record_date",
        ),
    )
