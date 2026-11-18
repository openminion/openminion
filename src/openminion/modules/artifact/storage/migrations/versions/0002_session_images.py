"""Bind selected images to sessions independently of content-addressed blobs."""

from openminion.modules.storage.migrations.alembic import (
    apply_ddl_statements,
    drop_sql_objects,
)

revision = "0002_session_images"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

DDL = (
    """CREATE TABLE IF NOT EXISTS session_images (
        image_ref TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        source_size_bytes INTEGER NOT NULL,
        width INTEGER NOT NULL,
        height INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        revoked_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_session_images_session ON session_images(session_id, revoked_at)",
    "CREATE INDEX IF NOT EXISTS idx_session_images_sha ON session_images(sha256, revoked_at)",
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=("session_images",),
        index_names=("idx_session_images_session", "idx_session_images_sha"),
    )
