from openminion.modules.storage.migrations.alembic import apply_ddl_statements


revision = "0007_workflow_observations"
down_revision = "0006_proposal_replay_proof"
branch_labels = None
depends_on = None


DDL = (
    """
    CREATE TABLE skill_workflow_observations (
        agent_id TEXT NOT NULL,
        source_run_ref TEXT NOT NULL,
        bundle_id TEXT NOT NULL UNIQUE,
        provenance_checksum TEXT NOT NULL,
        bundle_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (agent_id, source_run_ref)
    )
    """,
    """
    CREATE INDEX idx_skill_workflow_observations_agent
    ON skill_workflow_observations(agent_id, created_at)
    """,
)
DOWN_DDL = ("DROP TABLE skill_workflow_observations",)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    apply_ddl_statements(DOWN_DDL)
