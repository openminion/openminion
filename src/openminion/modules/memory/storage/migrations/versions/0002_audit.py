"""Add deletion audit columns to ``memory_records``."""

from openminion.modules.storage.migrations.alembic import (
    apply_ddl_statements,
    drop_sql_objects,
)


revision = "0002_audit"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


DDL = (
    "ALTER TABLE memory_records ADD COLUMN deleted_at TEXT",
    "ALTER TABLE memory_records ADD COLUMN deleted_reason TEXT",
)

DOWN_DDL = (
    "ALTER TABLE memory_records DROP COLUMN IF EXISTS deleted_at",
    "ALTER TABLE memory_records DROP COLUMN IF EXISTS deleted_reason",
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    drop_sql_objects(DOWN_DDL)
