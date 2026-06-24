from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence, Tuple

from openminion.modules.storage.record_store import RecordStore

from .sqlite import connect_database, resolve_database_path


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: Tuple[str, ...]


@dataclass(frozen=True)
class MigrationResult:
    applied_versions: Tuple[int, ...]
    current_version: int


class MigrationError(RuntimeError):
    """Raised when a schema migration fails or is invalid."""


DEFAULT_MIGRATIONS: Tuple[Migration, ...] = (
    Migration(
        version=1,
        name="bootstrap_core_tables",
        statements=(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                session_key TEXT NOT NULL UNIQUE,
                channel TEXT NOT NULL,
                target TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE INDEX idx_sessions_channel_target ON sessions(channel, target)
            """,
            """
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                body TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE INDEX idx_messages_session_created ON messages(session_id, created_at, id)
            """,
            """
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE INDEX idx_events_session_created ON events(session_id, created_at, id)
            """,
            """
            CREATE TABLE idempotency_keys (
                method TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL DEFAULT '',
                response_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'completed',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (method, idempotency_key)
            )
            """,
            """
            CREATE INDEX idx_idempotency_keys_created ON idempotency_keys(created_at)
            """,
        ),
    ),
    Migration(
        version=2,
        name="add_session_contexts_table",
        statements=(
            """
            CREATE TABLE session_contexts (
                session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                pinned_context TEXT NOT NULL DEFAULT '',
                rolling_summary TEXT NOT NULL DEFAULT '',
                compacted_until_rowid INTEGER NOT NULL DEFAULT 0,
                compacted_until_created_at TEXT NOT NULL DEFAULT '',
                compacted_until_message_id TEXT NOT NULL DEFAULT '',
                compacted_message_count INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE INDEX idx_session_contexts_updated ON session_contexts(updated_at)
            """,
        ),
    ),
    Migration(
        version=3,
        name="add_agent_runtime_and_a2a_tables",
        statements=(
            """
            CREATE TABLE daemon_registry (
                agent_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                config_path TEXT NOT NULL DEFAULT '',
                workspace_root TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'stopped',
                registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE INDEX idx_daemon_registry_status ON daemon_registry(status)
            """,
            """
            CREATE TABLE daemon_heartbeats (
                agent_id TEXT PRIMARY KEY,
                pid INTEGER NOT NULL DEFAULT 0,
                host TEXT NOT NULL DEFAULT '',
                port INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'idle',
                active_run_id TEXT NOT NULL DEFAULT '',
                last_heartbeat_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """,
            """
            CREATE INDEX idx_daemon_heartbeats_last_heartbeat ON daemon_heartbeats(last_heartbeat_at)
            """,
            """
            CREATE TABLE a2a_jobs (
                id TEXT PRIMARY KEY,
                from_agent TEXT NOT NULL DEFAULT '',
                to_agent TEXT NOT NULL DEFAULT '',
                message_type TEXT NOT NULL DEFAULT 'task',
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'new',
                idempotency_key TEXT NOT NULL DEFAULT '',
                lease_token TEXT NOT NULL DEFAULT '',
                lease_expires_at TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 3,
                priority INTEGER NOT NULL DEFAULT 0,
                visible_after TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                result_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE INDEX idx_a2a_jobs_to_agent_status ON a2a_jobs(to_agent, status, visible_after)
            """,
            """
            CREATE INDEX idx_a2a_jobs_idempotency ON a2a_jobs(to_agent, idempotency_key)
            """,
        ),
    ),
    Migration(
        version=4,
        name="add_vector_memory_tables",
        statements=(
            """
            CREATE TABLE memory_records (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                message_id TEXT,
                event_id INTEGER,
                chunk_ref TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                content_type TEXT NOT NULL DEFAULT 'text',
                scope TEXT NOT NULL DEFAULT 'session',
                agent_id TEXT NOT NULL DEFAULT '',
                project_id TEXT NOT NULL DEFAULT '',
                idempotency_key TEXT NOT NULL UNIQUE,
                importance INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE INDEX idx_memory_records_session ON memory_records(session_id, created_at)
            """,
            """
            CREATE INDEX idx_memory_records_idempotency ON memory_records(idempotency_key)
            """,
            """
            CREATE INDEX idx_memory_records_scope ON memory_records(scope, agent_id, project_id)
            """,
            """
            CREATE TABLE memory_vectors (
                id TEXT PRIMARY KEY,
                memory_record_id TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
                provider TEXT NOT NULL DEFAULT 'zvec',
                model TEXT NOT NULL DEFAULT '',
                embedding BLOB NOT NULL,
                embedding_dim INTEGER NOT NULL DEFAULT 1536,
                sync_status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_retry_at TEXT NOT NULL DEFAULT '',
                embedded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE INDEX idx_memory_vectors_record ON memory_vectors(memory_record_id)
            """,
            """
            CREATE INDEX idx_memory_vectors_status ON memory_vectors(sync_status, created_at)
            """,
        ),
    ),
    Migration(
        version=5,
        name="add_message_conversation_id",
        statements=(
            """
            ALTER TABLE messages
            ADD COLUMN conversation_id TEXT NOT NULL DEFAULT ''
            """,
            """
            CREATE INDEX idx_messages_session_conversation_created
            ON messages(session_id, conversation_id, created_at, id)
            """,
        ),
    ),
    Migration(
        version=6,
        name="add_message_thread_attach_ids",
        statements=(
            """
            ALTER TABLE messages
            ADD COLUMN thread_id TEXT NOT NULL DEFAULT ''
            """,
            """
            ALTER TABLE messages
            ADD COLUMN attach_id TEXT NOT NULL DEFAULT ''
            """,
            """
            CREATE INDEX idx_messages_session_conversation_thread_created
            ON messages(session_id, conversation_id, thread_id, created_at, id)
            """,
        ),
    ),
    Migration(
        version=7,
        name="add_session_lifecycle_columns",
        statements=(
            """
            ALTER TABLE sessions
            ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
            """,
            """
            ALTER TABLE sessions
            ADD COLUMN last_activity_at TEXT NOT NULL DEFAULT ''
            """,
            """
            ALTER TABLE sessions
            ADD COLUMN closed_at TEXT DEFAULT NULL
            """,
            """
            ALTER TABLE sessions
            ADD COLUMN expires_at TEXT DEFAULT NULL
            """,
        ),
    ),
    Migration(
        version=8,
        name="add_session_context_summary_short",
        statements=(
            """
            ALTER TABLE session_contexts
            ADD COLUMN summary_short TEXT NOT NULL DEFAULT ''
            """,
        ),
    ),
    Migration(
        version=9,
        name="add_room_participants_and_active_agent",
        statements=(
            """
            ALTER TABLE sessions
            ADD COLUMN active_agent_id TEXT DEFAULT NULL
            """,
            """
            CREATE TABLE room_participants (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                participant_type TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'participant',
                display_name TEXT NOT NULL DEFAULT '',
                joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                left_at TEXT DEFAULT NULL,
                UNIQUE(session_id, participant_type, participant_id)
            )
            """,
            """
            CREATE INDEX idx_room_participants_session_joined
            ON room_participants(session_id, joined_at, id)
            """,
            """
            CREATE INDEX idx_room_participants_lookup
            ON room_participants(session_id, participant_type, participant_id, left_at)
            """,
        ),
    ),
)


_MIGRATION_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def migrate_database(
    database_path: str | Path,
    migrations: Sequence[Migration] = DEFAULT_MIGRATIONS,
) -> MigrationResult:
    path = resolve_database_path(database_path)
    connection = connect_database(path)
    try:
        return run_migrations(connection, migrations=migrations)
    finally:
        connection.close()


def run_migrations(
    connection: sqlite3.Connection,
    migrations: Sequence[Migration] = DEFAULT_MIGRATIONS,
) -> MigrationResult:
    ordered_migrations = _normalize_migrations(migrations)
    _ensure_migration_ledger(connection)
    applied_versions = _load_applied_versions(connection)
    newly_applied: list[int] = []

    for migration in ordered_migrations:
        if migration.version in applied_versions:
            continue

        try:
            connection.execute("BEGIN")
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO migrations(version, name, applied_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (migration.version, migration.name),
            )
            connection.commit()
            applied_versions.add(migration.version)
            newly_applied.append(migration.version)
        except sqlite3.Error as exc:
            connection.rollback()
            raise MigrationError(
                f"Migration v{migration.version} ({migration.name}) failed: {exc}"
            ) from exc

    current_version = max(applied_versions) if applied_versions else 0
    return MigrationResult(
        applied_versions=tuple(newly_applied), current_version=current_version
    )


def _normalize_migrations(migrations: Iterable[Migration]) -> list[Migration]:
    ordered = sorted(migrations, key=lambda item: item.version)
    seen_versions: set[int] = set()
    normalized: list[Migration] = []

    for migration in ordered:
        if migration.version <= 0:
            raise MigrationError(
                f"Migration version must be positive: {migration.version}"
            )
        if migration.version in seen_versions:
            raise MigrationError(
                f"Duplicate migration version detected: {migration.version}"
            )
        if not migration.statements:
            raise MigrationError(f"Migration v{migration.version} has no statements")

        seen_versions.add(migration.version)
        normalized.append(migration)

    return normalized


def _ensure_migration_ledger(connection: sqlite3.Connection) -> None:
    connection.execute(_MIGRATION_LEDGER_SQL)
    connection.commit()


def _load_applied_versions(connection: sqlite3.Connection) -> set[int]:
    rows = connection.execute(
        "SELECT version FROM migrations ORDER BY version ASC"
    ).fetchall()
    versions: set[int] = set()
    for row in rows:
        if isinstance(row, sqlite3.Row):
            versions.add(int(row["version"]))
        else:
            versions.add(int(row[0]))
    return versions


def migrate_record_store(
    record_store: RecordStore,
    *,
    backend_type: str,
    migrations: Sequence[Migration] = DEFAULT_MIGRATIONS,
) -> MigrationResult:
    ordered_migrations = _normalize_migrations(migrations)
    _ensure_migration_ledger_store(record_store)
    applied_versions = _load_applied_versions_store(record_store)
    newly_applied: list[int] = []

    for migration in ordered_migrations:
        if migration.version in applied_versions:
            continue
        statements = _adapt_migration_statements(
            migration.statements,
            backend_type=backend_type,
        )
        try:
            with record_store.transaction():
                for statement in statements:
                    record_store.execute_count(statement)
                record_store.execute_count(
                    "INSERT INTO migrations(version, name, applied_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                    (migration.version, migration.name),
                )
        except Exception as exc:  # noqa: BLE001
            raise MigrationError(
                f"Migration v{migration.version} ({migration.name}) failed: {exc}"
            ) from exc
        applied_versions.add(migration.version)
        newly_applied.append(migration.version)

    current_version = max(applied_versions) if applied_versions else 0
    return MigrationResult(
        applied_versions=tuple(newly_applied),
        current_version=current_version,
    )


def _ensure_migration_ledger_store(record_store: RecordStore) -> None:
    record_store.execute_count(_MIGRATION_LEDGER_SQL)


def _load_applied_versions_store(record_store: RecordStore) -> set[int]:
    rows = record_store.query_dicts(
        "SELECT version FROM migrations ORDER BY version ASC"
    )
    return {int(row["version"]) for row in rows}


def _adapt_migration_statements(
    statements: Sequence[str],
    *,
    backend_type: str,
) -> tuple[str, ...]:
    if str(backend_type).strip().lower() != "postgres":
        return tuple(statements)
    adapted: list[str] = []
    for statement in statements:
        normalized = statement.replace(
            "id INTEGER PRIMARY KEY AUTOINCREMENT",
            "id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
        ).replace("embedding BLOB NOT NULL", "embedding BYTEA NOT NULL")
        adapted.append(normalized)
    return tuple(adapted)
