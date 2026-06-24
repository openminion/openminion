from openminion.modules.storage.migrations.alembic import (
    apply_ddl_statements,
    drop_sql_objects,
)


revision = "0003_audit"
down_revision = "0002_queue"
branch_labels = None
depends_on = None


DDL = (
    """
    CREATE TABLE IF NOT EXISTS skill_suggestion_audit (
        event_id TEXT PRIMARY KEY,
        proposal_id TEXT NOT NULL,
        signature TEXT NOT NULL,
        event_type TEXT NOT NULL,
        reason TEXT,
        outcome TEXT,
        surfaced_at TEXT NOT NULL
    )
    """,
    (
        "CREATE INDEX IF NOT EXISTS idx_skill_suggestion_audit_signature "
        "ON skill_suggestion_audit(signature, surfaced_at)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_skill_suggestion_audit_event "
        "ON skill_suggestion_audit(event_type, surfaced_at)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_skill_suggestion_audit_proposal "
        "ON skill_suggestion_audit(proposal_id, surfaced_at)"
    ),
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=("skill_suggestion_audit",),
        index_names=(
            "idx_skill_suggestion_audit_signature",
            "idx_skill_suggestion_audit_event",
            "idx_skill_suggestion_audit_proposal",
        ),
    )
