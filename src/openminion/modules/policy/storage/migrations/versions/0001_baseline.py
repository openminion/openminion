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
    CREATE TABLE IF NOT EXISTS policy_grants (
        grant_id TEXT PRIMARY KEY,
        subject_id TEXT NOT NULL,
        effect TEXT NOT NULL,
        tool TEXT NOT NULL,
        method TEXT NOT NULL,
        target_json TEXT NOT NULL DEFAULT '{}',
        risk_floor TEXT,
        duration_type TEXT NOT NULL,
        expires_at TEXT,
        session_id TEXT,
        invocation_hash TEXT,
        max_uses INTEGER,
        uses_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        revoked_at TEXT,
        reason TEXT,
        created_trace_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS policy_decisions (
        decision_id TEXT PRIMARY KEY,
        trace_id TEXT,
        session_id TEXT,
        agent_id TEXT,
        invocation_id TEXT,
        tool TEXT,
        method TEXT,
        decision TEXT NOT NULL,
        matched_grant_id TEXT,
        reason_code TEXT,
        risk_spec_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS policy_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_policy_decisions_session
    ON policy_decisions(session_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_policy_decisions_trace
    ON policy_decisions(trace_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_policy_grants_active
    ON policy_grants(subject_id, revoked_at, expires_at)
    """,
    "CREATE INDEX IF NOT EXISTS idx_policy_grants_invocation ON policy_grants(invocation_hash)",
    """
    CREATE INDEX IF NOT EXISTS idx_policy_grants_subject
    ON policy_grants(subject_id, tool, method)
    """,
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=("policy_grants", "policy_decisions", "policy_settings", "om_meta"),
        index_names=(
            "idx_policy_decisions_session",
            "idx_policy_decisions_trace",
            "idx_policy_grants_active",
            "idx_policy_grants_invocation",
            "idx_policy_grants_subject",
        ),
    )
