"""Append-only audit sink for authored-tool lifecycle events."""

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
from threading import RLock

from ..schemas import AuthoredToolAuditEventRow


class SQLiteToolAuthoringAuditSink:
    """SQLite-backed append-only sink for authored-tool audit events."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        with self._lock:
            if self._conn is None:
                conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tool_authoring_audit_events (
                        event_id TEXT PRIMARY KEY,
                        timestamp TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        target_kind TEXT NOT NULL,
                        target_id TEXT NOT NULL,
                        agent_id TEXT,
                        session_id TEXT,
                        version_hash TEXT,
                        details_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_tool_authoring_events_timestamp
                    ON tool_authoring_audit_events(timestamp)
                    """
                )
                conn.commit()
                self._conn = conn
        return self._conn

    def append_event(self, event: AuthoredToolAuditEventRow) -> None:
        conn = self._connect()
        payload = asdict(event)
        with self._lock:
            conn.execute(
                """
                INSERT OR REPLACE INTO tool_authoring_audit_events (
                    event_id, timestamp, event_type, target_kind, target_id,
                    agent_id, session_id, version_hash, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["event_id"],
                    payload["timestamp"],
                    payload["event_type"],
                    payload["target_kind"],
                    payload["target_id"],
                    payload["agent_id"],
                    payload["session_id"],
                    payload["version_hash"],
                    payload["details_json"],
                ),
            )
            conn.commit()

    def list_events(self) -> list[AuthoredToolAuditEventRow]:
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT event_id, timestamp, event_type, target_kind, target_id,
                   agent_id, session_id, version_hash, details_json
            FROM tool_authoring_audit_events
            ORDER BY timestamp ASC, event_id ASC
            """
        ).fetchall()
        return [
            AuthoredToolAuditEventRow(
                event_id=str(row["event_id"]),
                timestamp=str(row["timestamp"]),
                event_type=str(row["event_type"]),
                target_kind=str(row["target_kind"]),
                target_id=str(row["target_id"]),
                agent_id=None if row["agent_id"] is None else str(row["agent_id"]),
                session_id=(
                    None if row["session_id"] is None else str(row["session_id"])
                ),
                version_hash=(
                    None if row["version_hash"] is None else str(row["version_hash"])
                ),
                details_json=str(row["details_json"]),
            )
            for row in rows
        ]

    def close(self) -> None:
        if self._conn is None:
            return
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


def default_tool_authoring_audit_db_path(db_path: str | Path) -> Path:
    base = Path(db_path)
    if base.suffix:
        return base.with_name("audit.sqlite")
    return base / "audit.sqlite"


def encode_audit_details(details: dict[str, object]) -> str:
    return json.dumps(details, ensure_ascii=True, sort_keys=True)
