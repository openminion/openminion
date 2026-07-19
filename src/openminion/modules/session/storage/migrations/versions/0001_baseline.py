from openminion.modules.session.storage.schema import (
    BOOTSTRAP_SCHEMA,
    CRON_SCHEMA,
    EVENT_SOURCED_SCHEMA,
    SESSION_CONTINUATION_SCHEMA,
    V15_SCHEMA,
)
from openminion.modules.storage.migrations.alembic import (
    apply_ddl_statements,
    drop_sql_objects,
)


revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

DDL = (
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    *BOOTSTRAP_SCHEMA,
    *EVENT_SOURCED_SCHEMA,
    *SESSION_CONTINUATION_SCHEMA,
    *CRON_SCHEMA,
    *V15_SCHEMA,
)


def _ensure_column(table_name: str, column_name: str, ddl_tail: str) -> None:
    from alembic import op
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    try:
        columns = {str(column["name"]) for column in inspector.get_columns(table_name)}
    except Exception:  # noqa: BLE001
        return
    if column_name in columns:
        return
    bind.exec_driver_sql(
        f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {ddl_tail}'
    )


def upgrade() -> None:
    apply_ddl_statements(DDL)
    _ensure_column("sessions", "active_profile_version", "TEXT")


def downgrade() -> None:
    drop_sql_objects(
        table_names=(
            "om_meta",
            "sessions",
            "turns",
            "events",
            "working_state",
            "summaries",
            "summary_deltas",
            "session_events",
            "session_snapshots",
            "session_summaries",
            "cron_jobs",
            "cron_runs",
            "prompt_contexts",
            "prompt_checkpoints",
            "prompt_seed_bundles",
            "prompt_runs",
            "artifact_refs",
            "archive_refs",
        )
    )
