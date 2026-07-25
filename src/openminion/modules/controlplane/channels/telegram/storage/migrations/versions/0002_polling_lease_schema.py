from openminion.modules.storage.migrations.alembic import (
    apply_ddl_statements,
    drop_sql_objects,
)


revision = "0002_polling_lease_schema"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

DDL = (
    """
    CREATE TABLE IF NOT EXISTS telegram_polling_leases (
        account_id TEXT PRIMARY KEY,
        owner_pid INTEGER NOT NULL,
        process_start_marker TEXT NOT NULL,
        command TEXT NOT NULL,
        acquired_at_ts INTEGER NOT NULL,
        heartbeat_at_ts INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_telegram_polling_leases_heartbeat ON telegram_polling_leases(heartbeat_at_ts)",
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=("telegram_polling_leases",),
        index_names=("idx_telegram_polling_leases_heartbeat",),
    )
