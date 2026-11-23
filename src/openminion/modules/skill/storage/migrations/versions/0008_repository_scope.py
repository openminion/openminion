from openminion.modules.storage.migrations.alembic import apply_ddl_statements


revision = "0008_repository_scope"
down_revision = "0007_workflow_observations"
branch_labels = None
depends_on = None

DDL = ("ALTER TABLE skills ADD COLUMN repository_root TEXT",)
DOWN_DDL = ("ALTER TABLE skills DROP COLUMN repository_root",)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    apply_ddl_statements(DOWN_DDL)
