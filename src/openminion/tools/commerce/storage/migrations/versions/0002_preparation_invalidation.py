"""Persist preparation invalidation after user takeover."""

from openminion.modules.storage.migrations.alembic import apply_ddl_statements

revision = "0002_preparation_invalidation"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply_ddl_statements(
        (
            "ALTER TABLE commerce_preparations ADD COLUMN prepared_json TEXT",
            "ALTER TABLE commerce_preparations ADD COLUMN invalidated_at TEXT",
        )
    )


def downgrade() -> None:
    apply_ddl_statements(
        (
            "ALTER TABLE commerce_preparations DROP COLUMN IF EXISTS invalidated_at",
            "ALTER TABLE commerce_preparations DROP COLUMN IF EXISTS prepared_json",
        )
    )
