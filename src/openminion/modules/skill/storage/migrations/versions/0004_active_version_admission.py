from openminion.modules.storage.migrations.alembic import apply_ddl_statements


revision = "0004_active_version_admission"
down_revision = "0003_audit"
branch_labels = None
depends_on = None


DDL = (
    "ALTER TABLE skills ADD COLUMN active_version_hash TEXT",
    "ALTER TABLE skill_versions ADD COLUMN content_fingerprint TEXT NOT NULL DEFAULT ''",
    "UPDATE skill_versions SET content_fingerprint = 'legacy:' || version_hash WHERE content_fingerprint = ''",
    "CREATE INDEX IF NOT EXISTS idx_skill_versions_fingerprint ON skill_versions(skill_id, content_fingerprint)",
    """
    CREATE TABLE IF NOT EXISTS skill_version_admissions (
        skill_id TEXT NOT NULL,
        version_hash TEXT NOT NULL,
        state TEXT NOT NULL,
        target_status TEXT NOT NULL,
        content_fingerprint TEXT NOT NULL,
        authority_class TEXT NOT NULL,
        reviewer_id TEXT,
        reason TEXT,
        created_at TEXT NOT NULL,
        decided_at TEXT,
        PRIMARY KEY (skill_id, version_hash),
        FOREIGN KEY(skill_id, version_hash) REFERENCES skill_versions(skill_id, version_hash)
    )
    """,
    """
    UPDATE skills SET active_version_hash = (
        SELECT version_hash FROM skill_versions
        WHERE skill_versions.skill_id = skills.skill_id
        ORDER BY created_at DESC, version_hash DESC LIMIT 1
    ) WHERE active_version_hash IS NULL
    """,
    """
    INSERT INTO skill_version_admissions(
        skill_id, version_hash, state, target_status, content_fingerprint,
        authority_class, reviewer_id, reason, created_at, decided_at
    )
    SELECT s.skill_id, s.active_version_hash, 'legacy_grandfathered', s.status,
           sv.content_fingerprint, 'legacy_grandfathered', NULL,
           'pre-admission catalog version', s.updated_at, s.updated_at
    FROM skills s JOIN skill_versions sv
      ON sv.skill_id = s.skill_id AND sv.version_hash = s.active_version_hash
    ON CONFLICT(skill_id, version_hash) DO NOTHING
    """,
)

DOWN_DDL = (
    "DROP TABLE IF EXISTS skill_version_admissions",
    "DROP INDEX IF EXISTS idx_skill_versions_fingerprint",
    "ALTER TABLE skill_versions DROP COLUMN content_fingerprint",
    "ALTER TABLE skills DROP COLUMN active_version_hash",
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    apply_ddl_statements(DOWN_DDL)
