from openminion.modules.storage.migrations.alembic import (
    apply_ddl_statements,
    drop_sql_objects,
)


revision = "0002_index_audit"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

DDL = (
    "CREATE INDEX IF NOT EXISTS idx_cp_inbox_status_locked ON cp_inbox(status, locked_at)",
    "CREATE INDEX IF NOT EXISTS idx_cp_outbox_status_locked ON cp_outbox(status, locked_at)",
    "CREATE INDEX IF NOT EXISTS idx_cp_channel_subjects_channel_status ON cp_channel_subjects(channel, status)",
    "CREATE INDEX IF NOT EXISTS idx_cp_pairings_status_channel ON cp_pairings(status, channel)",
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    drop_sql_objects(
        index_names=(
            "idx_cp_pairings_status_channel",
            "idx_cp_channel_subjects_channel_status",
            "idx_cp_outbox_status_locked",
            "idx_cp_inbox_status_locked",
        )
    )
