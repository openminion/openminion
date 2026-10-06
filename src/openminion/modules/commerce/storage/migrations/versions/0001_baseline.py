"""Create commerce order and attempt storage."""

from openminion.modules.storage.migrations.alembic import (
    apply_ddl_statements,
    drop_sql_objects,
)

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

DDL = (
    """
    CREATE TABLE IF NOT EXISTS commerce_attempts (
        attempt_id TEXT PRIMARY KEY,
        subject_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        target_id TEXT NOT NULL,
        operation TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_digest TEXT NOT NULL,
        state TEXT NOT NULL,
        response_digest TEXT,
        provider_reference_digest TEXT,
        active INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(subject_id, kind, idempotency_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS commerce_preparations (
        preparation_id TEXT PRIMARY KEY,
        subject_id TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        preparation_attempt_id TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(subject_id, preparation_id),
        FOREIGN KEY(preparation_attempt_id) REFERENCES commerce_attempts(attempt_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS commerce_orders (
        order_id TEXT PRIMARY KEY,
        subject_id TEXT NOT NULL,
        preparation_id TEXT NOT NULL UNIQUE,
        provider_order_digest TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        lifecycle_json TEXT NOT NULL,
        placement_attempt_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(subject_id, order_id),
        FOREIGN KEY(subject_id, preparation_id)
            REFERENCES commerce_preparations(subject_id, preparation_id),
        FOREIGN KEY(placement_attempt_id) REFERENCES commerce_attempts(attempt_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS commerce_material_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        subject_id TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        lifecycle_json TEXT NOT NULL,
        cursor_digest TEXT,
        observed_at TEXT NOT NULL,
        UNIQUE(subject_id, target_type, target_id, payload_digest)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_commerce_attempt_stable_target
    ON commerce_attempts(subject_id, kind, target_id)
    WHERE kind IN ('preparation', 'placement')
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_commerce_active_action
    ON commerce_attempts(subject_id, target_id)
    WHERE kind = 'action' AND active = 1
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_commerce_snapshots_target
    ON commerce_material_snapshots(subject_id, target_type, target_id, observed_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=(
            "commerce_material_snapshots",
            "commerce_orders",
            "commerce_preparations",
            "commerce_attempts",
            "om_meta",
        ),
        index_names=(
            "idx_commerce_snapshots_target",
            "idx_commerce_active_action",
            "idx_commerce_attempt_stable_target",
        ),
    )
