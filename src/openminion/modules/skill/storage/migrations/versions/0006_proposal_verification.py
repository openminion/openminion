from openminion.modules.storage.migrations.alembic import apply_ddl_statements


revision = "0006_proposal_verification"
down_revision = "0005_verification_evidence"
branch_labels = None
depends_on = None


DDL = (
    "ALTER TABLE skill_proposals ADD COLUMN verification_evidence_json TEXT",
)

DOWN_DDL = (
    "ALTER TABLE skill_proposals DROP COLUMN verification_evidence_json",
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    apply_ddl_statements(DOWN_DDL)
