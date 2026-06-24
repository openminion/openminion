from sqlalchemy import inspect, text


revision = "0003_validity"
down_revision = "0002_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("memory_records")}
    if "event_time" not in columns:
        bind.exec_driver_sql("ALTER TABLE memory_records ADD COLUMN event_time TEXT")
    if "valid_to" not in columns:
        bind.exec_driver_sql("ALTER TABLE memory_records ADD COLUMN valid_to TEXT")
    bind.execute(
        text(
            """
            UPDATE memory_records
               SET event_time = created_at
             WHERE event_time IS NULL
            """
        )
    )


def downgrade() -> None:
    from alembic import op

    bind = op.get_bind()
    bind.exec_driver_sql("ALTER TABLE memory_records DROP COLUMN IF EXISTS valid_to")
    bind.exec_driver_sql("ALTER TABLE memory_records DROP COLUMN IF EXISTS event_time")
