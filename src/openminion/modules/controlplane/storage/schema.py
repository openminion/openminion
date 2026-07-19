from openminion.modules.controlplane.pairing.schema import CP_PAIRING_SCHEMA


MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "initial_schema",
        """
        CREATE TABLE IF NOT EXISTS cp_chat_bindings (
            chat_key        TEXT PRIMARY KEY,
            session_id      TEXT NOT NULL,
            active_agent_id TEXT,
            updated_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cp_chat_bindings_session
            ON cp_chat_bindings(session_id, updated_at);

        CREATE TABLE IF NOT EXISTS cp_users (
            user_key          TEXT PRIMARY KEY,
            role              TEXT NOT NULL,
            display_name      TEXT,
            profile_meta_json TEXT NOT NULL DEFAULT '{}',
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cp_users_role
            ON cp_users(role, updated_at);

        CREATE TABLE IF NOT EXISTS cp_inbound_messages (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id   TEXT NOT NULL,
            chat_key     TEXT NOT NULL,
            user_key     TEXT NOT NULL,
            session_id   TEXT,
            agent_id     TEXT,
            timestamp    TEXT NOT NULL,
            text         TEXT,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cp_inbound_chat_ts
            ON cp_inbound_messages(chat_key, timestamp, id);
        CREATE INDEX IF NOT EXISTS idx_cp_inbound_session_ts
            ON cp_inbound_messages(session_id, timestamp, id);
        CREATE INDEX IF NOT EXISTS idx_cp_inbound_msg_id
            ON cp_inbound_messages(message_id);

        CREATE TABLE IF NOT EXISTS cp_outbound_messages (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_key     TEXT NOT NULL,
            session_id   TEXT,
            agent_id     TEXT,
            timestamp    TEXT NOT NULL,
            text         TEXT,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cp_outbound_chat_ts
            ON cp_outbound_messages(chat_key, timestamp, id);
        CREATE INDEX IF NOT EXISTS idx_cp_outbound_session_ts
            ON cp_outbound_messages(session_id, timestamp, id);

        CREATE TABLE IF NOT EXISTS cp_audit_events (
            event_id     TEXT PRIMARY KEY,
            timestamp    TEXT NOT NULL,
            event_type   TEXT NOT NULL,
            severity     TEXT NOT NULL,
            outcome      TEXT NOT NULL,
            chat_key     TEXT,
            user_key     TEXT,
            session_id   TEXT,
            agent_id     TEXT,
            trace_id     TEXT NOT NULL,
            span_id      TEXT,
            details_json TEXT NOT NULL,
            error_json   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_cp_audit_type_ts
            ON cp_audit_events(event_type, timestamp);
        CREATE INDEX IF NOT EXISTS idx_cp_audit_session_ts
            ON cp_audit_events(session_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_cp_audit_trace_ts
            ON cp_audit_events(trace_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_cp_audit_chat_ts
            ON cp_audit_events(chat_key, timestamp);

        CREATE TABLE IF NOT EXISTS cp_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        """,
    ),
    (
        2,
        "durable_pipeline_v1",
        """
        CREATE TABLE IF NOT EXISTS cp_sessions (
            session_id   TEXT PRIMARY KEY,
            user_key     TEXT,
            chat_key     TEXT,
            title        TEXT,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cp_sessions_user_chat
            ON cp_sessions(user_key, chat_key, updated_at);

        CREATE TABLE IF NOT EXISTS cp_session_agents (
            session_id   TEXT PRIMARY KEY,
            agent_id     TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cp_session_agents_agent
            ON cp_session_agents(agent_id, updated_at);

        CREATE TABLE IF NOT EXISTS cp_inbox (
            inbox_id             TEXT PRIMARY KEY,
            channel              TEXT NOT NULL,
            chat_id              TEXT NOT NULL,
            channel_message_id   TEXT NOT NULL,
            user_id              TEXT NOT NULL,
            thread_id            TEXT,
            received_at          TEXT NOT NULL,
            payload_json         TEXT NOT NULL,
            status               TEXT NOT NULL,
            error                TEXT,
            attempts             INTEGER NOT NULL DEFAULT 0,
            locked_at            TEXT,
            lock_owner           TEXT,
            UNIQUE(channel, chat_id, channel_message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_cp_inbox_status_received
            ON cp_inbox(status, received_at);
        CREATE INDEX IF NOT EXISTS idx_cp_inbox_channel_chat
            ON cp_inbox(channel, chat_id);

        CREATE TABLE IF NOT EXISTS cp_outbox (
            outbox_id        TEXT PRIMARY KEY,
            channel          TEXT NOT NULL,
            chat_id          TEXT NOT NULL,
            thread_id        TEXT,
            reply_to         TEXT,
            payload_json     TEXT NOT NULL,
            status           TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            next_attempt_at  TEXT NOT NULL,
            attempts         INTEGER NOT NULL DEFAULT 0,
            last_error       TEXT,
            lock_owner       TEXT,
            locked_at        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_cp_outbox_status_next
            ON cp_outbox(status, next_attempt_at);
        CREATE INDEX IF NOT EXISTS idx_cp_outbox_channel_chat
            ON cp_outbox(channel, chat_id);

        CREATE TABLE IF NOT EXISTS cp_pairings (
            pairing_id    TEXT PRIMARY KEY,
            channel       TEXT NOT NULL,
            chat_id       TEXT NOT NULL,
            user_id       TEXT NOT NULL,
            session_id    TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            last_seen_at  TEXT NOT NULL,
            status        TEXT NOT NULL,
            scopes_json   TEXT,
            note          TEXT,
            UNIQUE(channel, chat_id)
        );

        CREATE TABLE IF NOT EXISTS cp_rate_limits (
            key_type      TEXT NOT NULL,
            key_id        TEXT NOT NULL,
            window_start  INTEGER NOT NULL,
            count         INTEGER NOT NULL DEFAULT 0,
            updated_at    TEXT NOT NULL,
            PRIMARY KEY(key_type, key_id, window_start)
        );
        CREATE INDEX IF NOT EXISTS idx_cp_rate_limits_lookup
            ON cp_rate_limits(key_type, key_id, window_start DESC);
        """,
    ),
    (
        3,
        "pending_clarify_store_v1",
        """
        CREATE TABLE IF NOT EXISTS cp_pending_clarify (
            session_id   TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cp_pending_clarify_updated
            ON cp_pending_clarify(updated_at);
        """,
    ),
    (
        4,
        "principal_identity_mapping_v1",
        """
        CREATE TABLE IF NOT EXISTS cp_principals (
            principal_id  TEXT PRIMARY KEY,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            meta_json     TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS cp_channel_subjects (
            principal_id  TEXT NOT NULL,
            channel       TEXT NOT NULL,
            subject_id    TEXT NOT NULL,
            status        TEXT NOT NULL,
            scopes_json   TEXT,
            note          TEXT,
            created_at    TEXT NOT NULL,
            last_seen_at  TEXT NOT NULL,
            meta_json     TEXT NOT NULL DEFAULT '{}',
            UNIQUE(channel, subject_id),
            FOREIGN KEY(principal_id) REFERENCES cp_principals(principal_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_cp_channel_subjects_principal
            ON cp_channel_subjects(principal_id, channel);
        CREATE INDEX IF NOT EXISTS idx_cp_channel_subjects_status
            ON cp_channel_subjects(status, channel);
        """,
    ),
    (
        5,
        "cross_channel_pairing_v1",
        CP_PAIRING_SCHEMA,
    ),
]


def list_migrations() -> list[str]:
    return [f"{version:04d}_{name}" for version, name, _ddl in MIGRATIONS]
