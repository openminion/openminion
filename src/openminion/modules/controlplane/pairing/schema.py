CP_PAIRING_SCHEMA = """
CREATE TABLE IF NOT EXISTS cp_pair_tokens (
    token_hash TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    token_hint TEXT NOT NULL,
    created_at_ts INTEGER NOT NULL,
    expires_at_ts INTEGER NOT NULL,
    used_at_ts INTEGER,
    expected_account_id TEXT,
    expected_chat_key TEXT,
    consumer_account_id TEXT,
    consumer_chat_key TEXT,
    scopes_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cp_pair_tokens_channel_expected
    ON cp_pair_tokens(channel, expected_account_id, expected_chat_key);
CREATE INDEX IF NOT EXISTS idx_cp_pair_tokens_expiry
    ON cp_pair_tokens(expires_at_ts);

CREATE TABLE IF NOT EXISTS cp_pair_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    account_id TEXT NOT NULL,
    chat_key TEXT,
    attempted_at_ts INTEGER NOT NULL,
    token_hash_prefix TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_cp_pair_attempts_channel_account_time
    ON cp_pair_attempts(channel, account_id, attempted_at_ts);
CREATE INDEX IF NOT EXISTS idx_cp_pair_attempts_channel_chat_time
    ON cp_pair_attempts(channel, chat_key, attempted_at_ts);
"""

CP_PAIRING_TABLES = ("cp_pair_tokens", "cp_pair_attempts")

__all__ = ["CP_PAIRING_SCHEMA", "CP_PAIRING_TABLES"]
