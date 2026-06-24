import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


_DDL_FORKS = """
CREATE TABLE IF NOT EXISTS session_forks (
    fork_id TEXT PRIMARY KEY,
    parent_session_id TEXT NOT NULL,
    new_session_id TEXT NOT NULL UNIQUE,
    snapshot_id TEXT NOT NULL,
    forked_at TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT ''
)
"""

_DDL_FORKS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_session_forks_parent "
    "ON session_forks(parent_session_id, forked_at)"
)


@dataclass(frozen=True)
class SessionForkRecord:
    """Typed fork relationship — composes over snapshot vocabulary."""

    fork_id: str
    parent_session_id: str
    new_session_id: str
    snapshot_id: str
    forked_at: str
    name: str = ""
    decision_action: str = "fork_thread"  # mirrors THREAD_DECISION_FORK


class _SnapshotCreator(Protocol):
    def create_snapshot(self, session_id: str, seq_upto: int | None = None) -> str: ...


@dataclass
class SessionForkAPI:
    """Composes snapshot + fork-edge persistence + new session id.

    Wraps an existing snapshot-creator (e.g. `SessionStore`) without
    modifying the store's contract.  The new session id is generated
    locally and stored alongside the snapshot id in `session_forks`.
    """

    snapshot_creator: _SnapshotCreator
    conn: sqlite3.Connection

    def __post_init__(self) -> None:
        self.conn.execute(_DDL_FORKS)
        self.conn.execute(_DDL_FORKS_IDX)
        self.conn.commit()

    def fork(
        self,
        session_id: str,
        *,
        new_name: str = "",
        seq_upto: int | None = None,
    ) -> SessionForkRecord:
        """Fork `session_id` at the current state.  Returns typed record."""

        snapshot_id = self.snapshot_creator.create_snapshot(
            session_id, seq_upto=seq_upto
        )
        new_session_id = f"sess-{uuid.uuid4().hex[:12]}"
        record = SessionForkRecord(
            fork_id=f"fork-{uuid.uuid4().hex[:12]}",
            parent_session_id=session_id,
            new_session_id=new_session_id,
            snapshot_id=snapshot_id,
            forked_at=datetime.now(timezone.utc).isoformat(),
            name=str(new_name or "").strip(),
        )
        self.conn.execute(
            "INSERT INTO session_forks(fork_id, parent_session_id, "
            "new_session_id, snapshot_id, forked_at, name) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                record.fork_id,
                record.parent_session_id,
                record.new_session_id,
                record.snapshot_id,
                record.forked_at,
                record.name,
            ),
        )
        self.conn.commit()
        return record

    def list_forks_of(self, session_id: str) -> list[SessionForkRecord]:
        cur = self.conn.execute(
            "SELECT fork_id, parent_session_id, new_session_id, snapshot_id, "
            "forked_at, name FROM session_forks WHERE parent_session_id = ? "
            "ORDER BY forked_at ASC",
            (session_id,),
        )
        return [_record_from_row(row) for row in cur.fetchall()]

    def lookup_fork(self, new_session_id: str) -> SessionForkRecord | None:
        cur = self.conn.execute(
            "SELECT fork_id, parent_session_id, new_session_id, snapshot_id, "
            "forked_at, name FROM session_forks WHERE new_session_id = ?",
            (new_session_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _record_from_row(row)


def _record_from_row(row: tuple[str, str, str, str, str, str]) -> SessionForkRecord:
    return SessionForkRecord(
        fork_id=row[0],
        parent_session_id=row[1],
        new_session_id=row[2],
        snapshot_id=row[3],
        forked_at=row[4],
        name=row[5],
    )


__all__ = [
    "SessionForkAPI",
    "SessionForkRecord",
    "_SnapshotCreator",
]
