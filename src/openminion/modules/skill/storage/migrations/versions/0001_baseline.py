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
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS skills (
        skill_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        status TEXT NOT NULL,
        scope TEXT NOT NULL,
        agent_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS skill_versions (
        skill_id TEXT NOT NULL,
        version_hash TEXT NOT NULL,
        source_artifact_ref TEXT NOT NULL,
        package_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (skill_id, version_hash),
        FOREIGN KEY(skill_id) REFERENCES skills(skill_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS skill_index (
        skill_id TEXT NOT NULL,
        version_hash TEXT NOT NULL,
        tags_json TEXT NOT NULL,
        tools_json TEXT NOT NULL,
        keywords_json TEXT NOT NULL,
        applies_to_json TEXT NOT NULL,
        PRIMARY KEY (skill_id, version_hash),
        FOREIGN KEY(skill_id, version_hash) REFERENCES skill_versions(skill_id, version_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS skill_runs (
        run_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        skill_id TEXT NOT NULL,
        version_hash TEXT NOT NULL,
        used_for TEXT NOT NULL,
        outcome TEXT NOT NULL,
        evidence_refs_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(skill_id, version_hash) REFERENCES skill_versions(skill_id, version_hash)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_skill_runs_session ON skill_runs(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_skill_runs_skill ON skill_runs(skill_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_skill_versions_skill ON skill_versions(skill_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_skills_scope ON skills(scope, agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status)",
    "CREATE INDEX IF NOT EXISTS idx_skills_updated ON skills(updated_at)",
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=(
            "om_meta",
            "skills",
            "skill_versions",
            "skill_index",
            "skill_runs",
        ),
        index_names=(
            "idx_skill_runs_session",
            "idx_skill_runs_skill",
            "idx_skill_versions_skill",
            "idx_skills_scope",
            "idx_skills_status",
            "idx_skills_updated",
        ),
    )
