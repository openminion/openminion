import datetime
import logging
import sqlite3

from .queries import (
    MIGRATION_V1,
    CREATE_MIGRATIONS_TABLE,
    GET_APPLIED_MIGRATIONS,
    RECORD_MIGRATION,
)

logger = logging.getLogger(__name__)


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _candidate_meta_migration(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "memory_candidates")
    if "meta_json" not in columns:
        conn.execute(
            "ALTER TABLE memory_candidates ADD COLUMN meta_json TEXT NOT NULL DEFAULT '{}'"
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_candidates_scope_status
          ON memory_candidates(proposed_scope, status, updated_at DESC)
        """
    )


def _record_last_hit_migration(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "memory_records")
    if "last_hit_at" not in columns:
        conn.execute("ALTER TABLE memory_records ADD COLUMN last_hit_at TEXT")


def _record_supersession_reason_migration(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "memory_records")
    if "supersession_reason" not in columns:
        conn.execute("ALTER TABLE memory_records ADD COLUMN supersession_reason TEXT")


def _record_delete_audit_migration(conn: sqlite3.Connection) -> None:
    """Add nullable delete-audit columns when missing."""
    columns = _table_columns(conn, "memory_records")
    if "deleted_at" not in columns:
        conn.execute("ALTER TABLE memory_records ADD COLUMN deleted_at TEXT")
    if "deleted_reason" not in columns:
        conn.execute("ALTER TABLE memory_records ADD COLUMN deleted_reason TEXT")


def _record_tiering_migration(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "memory_records")
    if "tier" not in columns:
        conn.execute(
            "ALTER TABLE memory_records ADD COLUMN tier TEXT NOT NULL DEFAULT 'working'"
        )
    if "access_count" not in columns:
        conn.execute(
            "ALTER TABLE memory_records ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_records_scope_tier_updated
          ON memory_records(scope, tier, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_tier_transitions (
            transition_id TEXT PRIMARY KEY,
            record_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            record_type TEXT NOT NULL,
            from_tier TEXT NOT NULL,
            to_tier TEXT NOT NULL,
            transition_reason TEXT NOT NULL,
            transition_at TEXT NOT NULL,
            access_count INTEGER NOT NULL DEFAULT 0,
            meta_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(record_id) REFERENCES memory_records(id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memory_tier_transitions_record
          ON memory_tier_transitions(record_id, transition_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memory_tier_transitions_scope
          ON memory_tier_transitions(scope, transition_at DESC)
        """
    )


def _record_bitemporal_migration(conn: sqlite3.Connection) -> None:
    """Record bitemporal migration helper."""
    columns = _table_columns(conn, "memory_records")
    if "event_time" not in columns:
        conn.execute("ALTER TABLE memory_records ADD COLUMN event_time TEXT")
    if "valid_to" not in columns:
        conn.execute("ALTER TABLE memory_records ADD COLUMN valid_to TEXT")
    conn.execute(
        """
        UPDATE memory_records
           SET event_time = created_at
         WHERE event_time IS NULL
        """
    )


def _record_goal_tag_migration(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "memory_records")
    if "goal_id" not in columns:
        conn.execute("ALTER TABLE memory_records ADD COLUMN goal_id TEXT")
    conn.execute(
        """
        UPDATE memory_records
           SET goal_id = json_extract(content_json, '$.goal_id')
         WHERE goal_id IS NULL
           AND json_valid(content_json)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_records_goal_id_updated
          ON memory_records(goal_id, updated_at DESC)
        """
    )


MIGRATIONS = [
    (1, "v1_baseline", MIGRATION_V1),
    (2, "v2_candidate_meta", _candidate_meta_migration),
    (3, "v3_record_last_hit", _record_last_hit_migration),
    (4, "v4_record_supersession_reason", _record_supersession_reason_migration),
    (5, "v5_record_tiering", _record_tiering_migration),
    (6, "v6_record_delete_audit", _record_delete_audit_migration),
    (7, "v7_record_bitemporal", _record_bitemporal_migration),
    (8, "v8_record_goal_tag", _record_goal_tag_migration),
]


def run_migrations(conn: sqlite3.Connection) -> None:
    """Run all pending SQLite migrations idempotently."""

    with conn:
        conn.execute(CREATE_MIGRATIONS_TABLE)

        cursor = conn.execute(GET_APPLIED_MIGRATIONS)
        applied_versions = {row[0] for row in cursor.fetchall()}

        for version, name, migration in MIGRATIONS:
            if version not in applied_versions:
                logger.info(f"Applying migration {version}: {name}")
                if callable(migration):
                    migration(conn)
                else:
                    statements = [s.strip() for s in migration.split(";") if s.strip()]
                    for stmt in statements:
                        conn.execute(stmt)

                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                conn.execute(RECORD_MIGRATION, (version, name, now))
