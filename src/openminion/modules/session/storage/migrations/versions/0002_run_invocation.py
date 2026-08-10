"""Add durable invocation and thread correlation to run records."""

from alembic import op
from sqlalchemy import inspect


revision = "0002_run_invocation"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        str(column["name"]) for column in inspect(bind).get_columns("run_records")
    }
    if "invocation_id" not in columns:
        bind.exec_driver_sql(
            'ALTER TABLE "run_records" ADD COLUMN "invocation_id" TEXT'
        )
    if "thread_id" not in columns:
        bind.exec_driver_sql('ALTER TABLE "run_records" ADD COLUMN "thread_id" TEXT')
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_run_records_session_thread "
        "ON run_records(session_id, thread_id, started_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_run_records_invocation "
        "ON run_records(invocation_id, started_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_run_records_invocation")
    op.execute("DROP INDEX IF EXISTS idx_run_records_session_thread")
