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
    CREATE TABLE IF NOT EXISTS tool_drafts (
        draft_id TEXT PRIMARY KEY,
        local_name TEXT NOT NULL,
        description TEXT NOT NULL,
        source_code TEXT NOT NULL,
        unit_tests_source TEXT NOT NULL,
        args_schema_json TEXT NOT NULL,
        returns_schema_json TEXT NOT NULL,
        requirements_json TEXT NOT NULL,
        dependencies_json TEXT NOT NULL,
        proposed_scope_tier TEXT,
        status TEXT NOT NULL,
        inspect_result_json TEXT,
        created_at TEXT NOT NULL,
        created_by_agent_id TEXT,
        created_by_session_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS authored_tools (
        tool_name TEXT PRIMARY KEY,
        local_name TEXT NOT NULL,
        version_number INTEGER NOT NULL,
        version_hash TEXT NOT NULL,
        source_code TEXT NOT NULL,
        unit_tests_source TEXT NOT NULL,
        args_schema_json TEXT NOT NULL,
        returns_schema_json TEXT NOT NULL,
        description TEXT NOT NULL,
        dependencies_json TEXT NOT NULL,
        tier TEXT NOT NULL,
        min_scope TEXT NOT NULL,
        policy_grant_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        created_by_agent_id TEXT,
        promoted_at TEXT,
        promoted_by TEXT,
        success_count INTEGER NOT NULL DEFAULT 0,
        failure_count INTEGER NOT NULL DEFAULT 0,
        last_invocation_at TEXT,
        removed_at TEXT,
        removed_by TEXT,
        UNIQUE(local_name, version_hash),
        UNIQUE(local_name, version_number)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_authoring_audit_events (
        event_id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        event_type TEXT NOT NULL,
        target_kind TEXT NOT NULL,
        target_id TEXT NOT NULL,
        agent_id TEXT,
        session_id TEXT,
        version_hash TEXT,
        details_json TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tool_authoring_events_timestamp
    ON tool_authoring_audit_events(timestamp)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tool_authoring_events_target
    ON tool_authoring_audit_events(target_kind, target_id)
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
        index_names=(
            "idx_tool_authoring_events_timestamp",
            "idx_tool_authoring_events_target",
        ),
        table_names=(
            "tool_drafts",
            "authored_tools",
            "tool_authoring_audit_events",
            "om_meta",
        ),
    )
