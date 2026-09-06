from openminion.modules.storage.migrations.alembic import apply_ddl_statements


revision = "0005_verification_evidence"
down_revision = "0004_active_version_admission"
branch_labels = None
depends_on = None


DDL = (
    "ALTER TABLE skill_version_admissions ADD COLUMN verification_check TEXT",
    "ALTER TABLE skill_version_admissions ADD COLUMN verification_result TEXT",
    "ALTER TABLE skill_version_admissions ADD COLUMN verification_evidence_ref TEXT",
    "ALTER TABLE skill_version_admissions ADD COLUMN verification_reviewer_id TEXT",
)

DOWN_DDL = (
    "ALTER TABLE skill_version_admissions DROP COLUMN verification_reviewer_id",
    "ALTER TABLE skill_version_admissions DROP COLUMN verification_evidence_ref",
    "ALTER TABLE skill_version_admissions DROP COLUMN verification_result",
    "ALTER TABLE skill_version_admissions DROP COLUMN verification_check",
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    apply_ddl_statements(DOWN_DDL)
