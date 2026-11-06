from openminion.modules.storage.migrations.alembic import (
    apply_ddl_statements,
    drop_sql_objects,
    require_postgres_column_types,
)


revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

DDL = (
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        timestamp REAL NOT NULL,
        data TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_session ON events(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_turn ON events(session_id, turn_id)",
)

# PostgreSQL gets the column types PostgresTelemetryStore creates: a SERIAL id and
# a DOUBLE PRECISION timestamp (REAL is a 4-byte float there).
POSTGRES_DDL = (
    """
    CREATE TABLE IF NOT EXISTS events (
        id SERIAL PRIMARY KEY,
        session_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        timestamp DOUBLE PRECISION NOT NULL,
        data TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_session ON events(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_turn ON events(session_id, turn_id)",
)

# An existing events table with other types for these columns is not adopted.
POSTGRES_COLUMN_TYPES = {"events": {"id": "SERIAL", "timestamp": "DOUBLE PRECISION"}}


def upgrade() -> None:
    from alembic import op

    if op.get_bind().dialect.name != "postgresql":
        apply_ddl_statements(DDL)
        return
    require_postgres_column_types(POSTGRES_COLUMN_TYPES)
    apply_ddl_statements(POSTGRES_DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=("events", "om_meta"),
        index_names=("idx_session", "idx_turn"),
    )
