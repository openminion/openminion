from openminion.modules.storage.migrations.alembic import (
    apply_ddl_statements,
    drop_sql_objects,
)


revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

DDL = (
    """
    CREATE TABLE IF NOT EXISTS compression_runs (
        run_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        method_id TEXT NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        ratio REAL NOT NULL,
        compression_hash TEXT NOT NULL,
        empty_augmentation INTEGER NOT NULL,
        fallback_used INTEGER NOT NULL,
        policy_hash TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        output_hash TEXT NOT NULL,
        engine_version TEXT NOT NULL,
        tokenizer_id TEXT NOT NULL,
        scorer_version TEXT NOT NULL,
        warnings TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dropped_reasons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        count INTEGER NOT NULL,
        FOREIGN KEY (run_id) REFERENCES compression_runs(run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS compression_failures (
        failure_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        error_code TEXT NOT NULL,
        message TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
)

POSTGRES_DDL = (
    """
    CREATE TABLE IF NOT EXISTS compression_runs (
        run_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        method_id TEXT NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        ratio DOUBLE PRECISION NOT NULL,
        compression_hash TEXT NOT NULL,
        empty_augmentation INTEGER NOT NULL,
        fallback_used INTEGER NOT NULL,
        policy_hash TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        output_hash TEXT NOT NULL,
        engine_version TEXT NOT NULL,
        tokenizer_id TEXT NOT NULL,
        scorer_version TEXT NOT NULL,
        warnings TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dropped_reasons (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        run_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        count INTEGER NOT NULL,
        FOREIGN KEY (run_id) REFERENCES compression_runs(run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS compression_failures (
        failure_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        error_code TEXT NOT NULL,
        message TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
)


def upgrade() -> None:
    from alembic import op

    bind = op.get_bind()
    apply_ddl_statements(POSTGRES_DDL if bind.dialect.name == "postgresql" else DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=(
            "compression_runs",
            "dropped_reasons",
            "compression_failures",
            "om_meta",
        )
    )
