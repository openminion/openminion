"""Add indexed telemetry v2 envelope fields."""

from alembic import op
from sqlalchemy import inspect


revision = "0002_event_v2"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


_COLUMNS = {
    "schema_version": "TEXT",
    "event_id": "TEXT",
    "trace_key": "TEXT",
    "invocation_id": "TEXT",
    "execution_id": "TEXT",
    "agent_id": "TEXT",
    "mode": "TEXT",
}


def upgrade() -> None:
    bind = op.get_bind()
    columns = {str(column["name"]) for column in inspect(bind).get_columns("events")}
    for name, column_type in _COLUMNS.items():
        if name not in columns:
            bind.exec_driver_sql(
                f'ALTER TABLE "events" ADD COLUMN "{name}" {column_type}'
            )
    op.create_index(
        "idx_events_event_id",
        "events",
        ["event_id"],
        unique=True,
        if_not_exists=True,
    )
    op.create_index(
        "idx_events_invocation_time",
        "events",
        ["invocation_id", "timestamp"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_events_execution_time",
        "events",
        ["execution_id", "timestamp"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_events_execution_time", table_name="events", if_exists=True)
    op.drop_index("idx_events_invocation_time", table_name="events", if_exists=True)
    op.drop_index("idx_events_event_id", table_name="events", if_exists=True)
