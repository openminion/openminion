from alembic import op


revision = "0003_session_detached_artifacts"
down_revision = "0002_run_invocation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS session_detached_artifacts (
          session_id TEXT NOT NULL,
          artifact_ref TEXT NOT NULL,
          event_id TEXT NOT NULL,
          event_seq INTEGER NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(session_id, artifact_ref),
          FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_detached_artifacts_session_seq "
        "ON session_detached_artifacts(session_id, event_seq)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_session_detached_artifacts_session_seq")
    op.execute("DROP TABLE IF EXISTS session_detached_artifacts")
