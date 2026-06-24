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
    CREATE TABLE IF NOT EXISTS telegram_poll_state (
        account_id TEXT PRIMARY KEY,
        last_update_id INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_pair_tokens (
        token_hash TEXT PRIMARY KEY,
        token_hint TEXT NOT NULL,
        created_at_ts INTEGER NOT NULL,
        expires_at_ts INTEGER NOT NULL,
        used_at_ts INTEGER,
        expected_user_id INTEGER,
        expected_chat_id INTEGER,
        scopes_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_pair_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        attempted_at_ts INTEGER NOT NULL,
        token_hash_prefix TEXT NOT NULL,
        user_id INTEGER,
        chat_id INTEGER,
        outcome TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_pending_clarify (
        chat_id INTEGER NOT NULL,
        topic_id INTEGER NOT NULL DEFAULT 0,
        clarify_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        trace_id TEXT NOT NULL,
        questions_json TEXT NOT NULL,
        created_at_ts INTEGER NOT NULL,
        updated_at_ts INTEGER NOT NULL,
        PRIMARY KEY(chat_id, topic_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_telegram_pair_attempts_chat_ts ON telegram_pair_attempts(chat_id, attempted_at_ts)",
    "CREATE INDEX IF NOT EXISTS idx_telegram_pair_attempts_ts ON telegram_pair_attempts(attempted_at_ts)",
    "CREATE INDEX IF NOT EXISTS idx_telegram_pair_attempts_user_ts ON telegram_pair_attempts(user_id, attempted_at_ts)",
    "CREATE INDEX IF NOT EXISTS idx_telegram_pair_tokens_expiry ON telegram_pair_tokens(expires_at_ts)",
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=(
            "telegram_poll_state",
            "telegram_pair_tokens",
            "telegram_pair_attempts",
            "telegram_pending_clarify",
            "om_meta",
        ),
        index_names=(
            "idx_telegram_pair_attempts_chat_ts",
            "idx_telegram_pair_attempts_ts",
            "idx_telegram_pair_attempts_user_ts",
            "idx_telegram_pair_tokens_expiry",
        ),
    )
