"""Modules a2a storage migrations versions 0003 audit archive."""

from __future__ import annotations

from openminion.modules.storage.migrations.alembic import drop_sql_objects


revision = "0003_archive"
down_revision = "0002_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS a2a_audit_archive (
            id BIGSERIAL PRIMARY KEY,
            record_date DATE NOT NULL,
            source_file TEXT NOT NULL,
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
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_a2a_audit_archive_record_date "
        "ON a2a_audit_archive(record_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_a2a_audit_archive_trace "
        "ON a2a_audit_archive(trace_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_a2a_audit_archive_ts ON a2a_audit_archive(ts)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_a2a_audit_archive_method "
        "ON a2a_audit_archive(method)"
    )


def downgrade() -> None:
    drop_sql_objects(
        table_names=("a2a_audit_archive",),
        index_names=(
            "idx_a2a_audit_archive_record_date",
            "idx_a2a_audit_archive_trace",
            "idx_a2a_audit_archive_ts",
            "idx_a2a_audit_archive_method",
        ),
    )
