"""Persist placement authorization and provider-attempt count."""

from openminion.modules.storage.migrations.alembic import apply_ddl_statements

revision = "0003_placement_attempt_authorization"
down_revision = "0002_preparation_invalidation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply_ddl_statements(
        (
            "ALTER TABLE commerce_attempts ADD COLUMN authorization_hash TEXT",
            "ALTER TABLE commerce_attempts ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
        )
    )


def downgrade() -> None:
    apply_ddl_statements(
        (
            "ALTER TABLE commerce_attempts DROP COLUMN IF EXISTS attempt_count",
            "ALTER TABLE commerce_attempts DROP COLUMN IF EXISTS authorization_hash",
        )
    )
