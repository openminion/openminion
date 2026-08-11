"""Add bounded telemetry inspection indexes."""

from alembic import op


revision = "0003_debug_query_indexes"
down_revision = "0002_event_v2"
branch_labels = None
depends_on = None

_INDEXES = (
    (
        "idx_events_type_time_invocation_id",
        ["event_type", "timestamp", "invocation_id", "id"],
    ),
    ("idx_events_invocation_time_id", ["invocation_id", "timestamp", "id"]),
    (
        "idx_events_session_turn_invocation_id",
        ["session_id", "turn_id", "invocation_id", "id"],
    ),
)


def upgrade() -> None:
    for name, columns in _INDEXES:
        op.create_index(name, "events", columns, if_not_exists=True)


def downgrade() -> None:
    for name, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name="events", if_exists=True)
