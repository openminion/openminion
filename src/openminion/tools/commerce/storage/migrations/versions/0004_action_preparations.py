"""Persist exact subject-owned order action preparations."""

from openminion.modules.storage.migrations.alembic import (
    apply_ddl_statements,
    drop_sql_objects,
)

revision = "0004_action_preparations"
down_revision = "0003_placement_attempt_authorization"
branch_labels = None
depends_on = None

DDL = (
    """
    CREATE TABLE IF NOT EXISTS commerce_action_preparations (
        action_id TEXT PRIMARY KEY,
        subject_id TEXT NOT NULL,
        order_id TEXT NOT NULL,
        prepared_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        invalidated_at TEXT,
        UNIQUE(subject_id, action_id),
        FOREIGN KEY(subject_id, order_id)
            REFERENCES commerce_orders(subject_id, order_id)
    )
    """,
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    drop_sql_objects(table_names=("commerce_action_preparations",))
