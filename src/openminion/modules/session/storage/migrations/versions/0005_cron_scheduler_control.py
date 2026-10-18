from alembic import op


revision = "0005_cron_scheduler_control"
down_revision = "0004_task_plan_step_start"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS cron_scheduler_control (
          singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
          paused       INTEGER NOT NULL DEFAULT 0,
          reason       TEXT,
          actor        TEXT,
          updated_at   TEXT NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cron_scheduler_control")
