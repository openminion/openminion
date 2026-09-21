from openminion.modules.storage.migrations.alembic import apply_ddl_statements


revision = "0004_task_plan_step_start"
down_revision = "0003_cron_coordination"
branch_labels = None
depends_on = None


DDL = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_task_plan_single_step_start
      ON session_events(session_id, task_id)
      WHERE event_type = 'task_plan.step_started'
    """,
)
DOWN_DDL = ("DROP INDEX IF EXISTS idx_task_plan_single_step_start",)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    apply_ddl_statements(DOWN_DDL)
