from alembic import op
from sqlalchemy import inspect


revision = "0003_cron_coordination"
down_revision = "0002_run_invocation"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {
        str(column["name"]) for column in inspect(op.get_bind()).get_columns(table_name)
    }


def upgrade() -> None:
    bind = op.get_bind()
    job_columns = _columns("cron_jobs")
    for name, definition in (
        ("concurrency_key", "TEXT"),
        ("max_attempts", "INTEGER NOT NULL DEFAULT 3"),
        ("retry_backoff_s", "INTEGER NOT NULL DEFAULT 30"),
    ):
        if name not in job_columns:
            bind.exec_driver_sql(
                f'ALTER TABLE "cron_jobs" ADD COLUMN "{name}" {definition}'
            )

    run_columns = _columns("cron_runs")
    for name, definition in (
        ("available_at", "TEXT"),
        ("output_json", "TEXT NOT NULL DEFAULT '{}'"),
    ):
        if name not in run_columns:
            bind.exec_driver_sql(
                f'ALTER TABLE "cron_runs" ADD COLUMN "{name}" {definition}'
            )

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cron_jobs_concurrency_key "
        "ON cron_jobs(concurrency_key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cron_runs_state_available "
        "ON cron_runs(state, available_at, due_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS cron_scope_state (
          concurrency_key     TEXT PRIMARY KEY,
          last_success_run_id TEXT NOT NULL,
          last_success_at     TEXT NOT NULL,
          watermark_json      TEXT NOT NULL DEFAULT '{}',
          updated_at          TEXT NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cron_scope_state")
    op.execute("DROP INDEX IF EXISTS idx_cron_runs_state_available")
    op.execute("DROP INDEX IF EXISTS idx_cron_jobs_concurrency_key")
