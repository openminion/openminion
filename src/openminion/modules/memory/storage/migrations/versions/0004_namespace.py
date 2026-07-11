"""Persist the canonical typed namespace on memory records."""

from sqlalchemy import inspect


revision = "0004_namespace"
down_revision = "0003_validity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("memory_records")}
    if "namespace_json" not in columns:
        bind.exec_driver_sql("ALTER TABLE memory_records ADD COLUMN namespace_json TEXT")


def downgrade() -> None:
    from alembic import op

    op.get_bind().exec_driver_sql(
        "ALTER TABLE memory_records DROP COLUMN IF EXISTS namespace_json"
    )
