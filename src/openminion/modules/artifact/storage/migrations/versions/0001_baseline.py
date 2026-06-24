"""Modules artifact storage migrations versions 0001 baseline."""

from __future__ import annotations

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
    CREATE TABLE IF NOT EXISTS artifacts (
        sha256 TEXT PRIMARY KEY,
        size_bytes INTEGER NOT NULL,
        mime TEXT NOT NULL,
        created_at TEXT NOT NULL,
        original_name TEXT,
        original_path TEXT,
        label TEXT,
        session_id TEXT,
        trace_id TEXT,
        agent_id TEXT,
        encoding TEXT,
        deleted_at TEXT,
        meta_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifact_views (
        raw_sha256 TEXT NOT NULL,
        view_type TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        policy_hash TEXT NOT NULL DEFAULT '',
        view_sha256 TEXT,
        view_path TEXT,
        mime TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        deleted_at TEXT,
        PRIMARY KEY (raw_sha256, view_type, schema_version, policy_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aliases (
        alias TEXT PRIMARY KEY,
        sha256 TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT,
        meta_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reference_edges (
        ref_id TEXT PRIMARY KEY,
        owner_type TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL,
        deleted_at TEXT,
        UNIQUE(owner_type, owner_id, sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_aliases_updated ON aliases(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_agent ON artifacts(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_created ON artifacts(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_deleted ON artifacts(deleted_at)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_label ON artifacts(label)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_mime ON artifacts(mime)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_name ON artifacts(original_name)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_size ON artifacts(size_bytes)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_trace ON artifacts(trace_id)",
    "CREATE INDEX IF NOT EXISTS idx_ref_edges_owner ON reference_edges(owner_type, owner_id, deleted_at)",
    "CREATE INDEX IF NOT EXISTS idx_ref_edges_sha ON reference_edges(sha256, deleted_at)",
    "CREATE INDEX IF NOT EXISTS idx_views_deleted ON artifact_views(deleted_at)",
    "CREATE INDEX IF NOT EXISTS idx_views_raw ON artifact_views(raw_sha256)",
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=(
            "artifacts",
            "artifact_views",
            "aliases",
            "reference_edges",
            "om_meta",
        ),
        index_names=(
            "idx_aliases_updated",
            "idx_artifacts_agent",
            "idx_artifacts_created",
            "idx_artifacts_deleted",
            "idx_artifacts_label",
            "idx_artifacts_mime",
            "idx_artifacts_name",
            "idx_artifacts_session",
            "idx_artifacts_size",
            "idx_artifacts_trace",
            "idx_ref_edges_owner",
            "idx_ref_edges_sha",
            "idx_views_deleted",
            "idx_views_raw",
        ),
    )
