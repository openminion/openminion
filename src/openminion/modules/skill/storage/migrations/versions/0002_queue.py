from openminion.modules.storage.migrations.alembic import (
    apply_ddl_statements,
    drop_sql_objects,
)


revision = "0002_queue"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


DDL = (
    """
    CREATE TABLE IF NOT EXISTS skill_proposals (
        proposal_id TEXT PRIMARY KEY,
        source_task_shape_ref TEXT NOT NULL,
        proposer_policy_id TEXT NOT NULL,
        proposed_at TEXT NOT NULL,
        proposal_json TEXT NOT NULL,
        queue_state TEXT NOT NULL,
        applied_addition_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS skill_proposal_reviews (
        proposal_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        reviewer_id TEXT NOT NULL,
        review_policy_id TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        review_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(proposal_id) REFERENCES skill_proposals(proposal_id)
    )
    """,
    (
        "CREATE INDEX IF NOT EXISTS idx_skill_proposals_state "
        "ON skill_proposals(queue_state, created_at)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_skill_proposals_shape "
        "ON skill_proposals(source_task_shape_ref)"
    ),
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=(
            "skill_proposal_reviews",
            "skill_proposals",
        ),
        index_names=(
            "idx_skill_proposals_state",
            "idx_skill_proposals_shape",
        ),
    )
