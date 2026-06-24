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
    CREATE TABLE IF NOT EXISTS identity_profiles (
        agent_id TEXT PRIMARY KEY,
        profile_json TEXT NOT NULL,
        profile_revision INTEGER NOT NULL,
        profile_version TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS identity_snippet_cache (
        cache_key TEXT PRIMARY KEY,
        snippet_text TEXT NOT NULL,
        used_tokens INTEGER,
        used_chars INTEGER,
        sections_json TEXT,
        included_fields_json TEXT,
        omitted_fields_json TEXT,
        warnings_json TEXT NOT NULL DEFAULT '[]',
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_identity_profiles_updated_at ON identity_profiles(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_identity_snippet_cache_updated_at ON identity_snippet_cache(updated_at)",
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=("identity_profiles", "identity_snippet_cache", "om_meta"),
        index_names=(
            "idx_identity_profiles_updated_at",
            "idx_identity_snippet_cache_updated_at",
        ),
    )
