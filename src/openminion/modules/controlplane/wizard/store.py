import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from enum import Enum


class WizardState(Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class WizardSession:
    """Data model representing a wizard session state."""

    wizard_id: str
    command_name: str
    state: WizardState
    step: int
    total_steps: int
    session_data: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    user_key: Optional[str] = None
    chat_key: Optional[str] = None
    session_id: Optional[str] = None

    draft_result: Optional[Dict[str, Any]] = field(default_factory=dict)
    timeout_at: Optional[datetime] = None


class WizardStore:
    """Base class for wizard session storage implementations."""

    DEFAULT_TIMEOUT = timedelta(minutes=30)

    async def init(self):
        """Initialize the store."""
        return None

    async def create_session(
        self,
        command_name: str,
        step: int = 1,
        total_steps: int = 1,
        user_key: Optional[str] = None,
        chat_key: Optional[str] = None,
        session_id: Optional[str] = None,
        timeout_duration: Optional[timedelta] = None,
    ) -> WizardSession:
        """Create a new wizard session with timeout."""
        wizard_id = str(uuid.uuid4())
        timeout_dur = timeout_duration or self.DEFAULT_TIMEOUT
        session = WizardSession(
            wizard_id=wizard_id,
            command_name=command_name,
            state=WizardState.ACTIVE,
            step=step,
            total_steps=total_steps,
            user_key=user_key,
            chat_key=chat_key,
            session_id=session_id,
            timeout_at=datetime.now(timezone.utc) + timeout_dur,
        )
        await self._save_raw_session(session)
        return session

    async def is_session_expired(self, session: WizardSession) -> bool:
        """Check if a session has expired based on timeout."""
        if session.timeout_at is None:
            return False
        return datetime.now(timezone.utc) > session.timeout_at

    async def get_session(self, wizard_id: str) -> Optional[WizardSession]:
        """Retrieve a wizard session by ID, checking for expiration."""
        session = await self._get_raw_session(wizard_id)
        if not session:
            return None

        if await self.is_session_expired(session):
            await self.timeout_session(wizard_id)
            return None

        return session

    async def update_session_state(
        self, wizard_id: str, new_state: WizardState, **updates
    ) -> Optional[WizardSession]:
        """Update session state and optional data fields. Updates refresh timeout."""
        session = await self.get_session(wizard_id)
        if not session:
            return None

        for key, value in updates.items():
            setattr(session, key, value)

        session.state = new_state
        session.updated_at = datetime.now(timezone.utc)

        if session.state == WizardState.ACTIVE:
            session.timeout_at = datetime.now(timezone.utc) + self.DEFAULT_TIMEOUT

        await self.save_session(session)
        return session

    async def save_session(self, session: WizardSession) -> bool:
        """Save a wizard session."""
        session.updated_at = datetime.now(timezone.utc)
        if session.state == WizardState.ACTIVE:
            session.timeout_at = datetime.now(timezone.utc) + self.DEFAULT_TIMEOUT
        await self._save_raw_session(session)
        return True

    async def timeout_session(self, wizard_id: str) -> bool:
        """Mark a session as timed out."""
        session = await self._get_raw_session(wizard_id)
        if not session:
            return False

        session.state = WizardState.TIMEOUT
        session.updated_at = datetime.now(timezone.utc)
        await self._save_raw_session(session)
        return True

    async def get_active_sessions_for_user(self, user_key: str) -> list[WizardSession]:
        """Get all active sessions for a specific user, filtering expired ones."""
        sessions = await self._get_raw_sessions_by_user(user_key)
        return await self._collect_active_sessions(sessions)

    async def get_active_sessions_for_chat(self, chat_key: str) -> list[WizardSession]:
        """Get all active sessions for a specific chat/channel, filtering expired ones."""
        sessions = await self._get_raw_sessions_by_chat(chat_key)
        return await self._collect_active_sessions(sessions)

    async def _collect_active_sessions(
        self, sessions: list[WizardSession]
    ) -> list[WizardSession]:
        valid_sessions = []
        for session in sessions:
            if not await self.is_session_expired(session):
                if session.state == WizardState.ACTIVE:
                    valid_sessions.append(session)
                continue
            await self.timeout_session(session.wizard_id)
        return valid_sessions

    async def expire_overdue(self) -> int:
        """Flip ACTIVE sessions whose ``timeout_at`` has passed to TIMEOUT."""
        raise NotImplementedError

    async def _get_raw_session(self, wizard_id: str) -> Optional[WizardSession]:
        """Internal implementation to get session without expiration check."""
        raise NotImplementedError

    async def _save_raw_session(self, session: WizardSession) -> bool:
        """Internal implementation to save session."""
        raise NotImplementedError

    async def _get_raw_sessions_by_user(self, user_key: str) -> list[WizardSession]:
        """Internal implementation to get sessions for user without expiration check."""
        raise NotImplementedError

    async def _get_raw_sessions_by_chat(self, chat_key: str) -> list[WizardSession]:
        """Internal implementation to get sessions for chat without expiration check."""
        raise NotImplementedError

    async def close(self):
        """Close resources used by the store."""
        pass


class InMemoryWizardStore(WizardStore):
    """In-memory implementation of wizard session storage."""

    def __init__(self):
        self._sessions: Dict[str, WizardSession] = {}

    async def init(self):
        """Initialize the in-memory store."""
        return None

    async def _get_raw_session(self, wizard_id: str) -> Optional[WizardSession]:
        """Get session without expiration check."""
        return self._sessions.get(wizard_id)

    async def _save_raw_session(self, session: WizardSession) -> bool:
        self._sessions[session.wizard_id] = session
        return True

    async def _get_raw_sessions_by_user(self, user_key: str) -> list[WizardSession]:
        """Get sessions for user without expiration check."""
        return [s for s in self._sessions.values() if s.user_key == user_key]

    async def _get_raw_sessions_by_chat(self, chat_key: str) -> list[WizardSession]:
        """Get sessions for chat without expiration check."""
        return [s for s in self._sessions.values() if s.chat_key == chat_key]

    async def delete_session(self, wizard_id: str) -> bool:
        """Delete a wizard session."""
        return self._sessions.pop(wizard_id, None) is not None

    async def expire_overdue(self) -> int:
        """Flip in-memory ACTIVE sessions past their timeout to TIMEOUT."""
        now = datetime.now(timezone.utc)
        flipped = 0
        for session in self._sessions.values():
            if (
                session.state == WizardState.ACTIVE
                and session.timeout_at is not None
                and session.timeout_at < now
            ):
                session.state = WizardState.TIMEOUT
                session.updated_at = now
                flipped += 1
        return flipped


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS cp_wizard_sessions (
    wizard_id        TEXT PRIMARY KEY,
    command_name     TEXT NOT NULL,
    state            TEXT NOT NULL,
    step             INTEGER NOT NULL,
    total_steps      INTEGER NOT NULL,
    session_data_json TEXT NOT NULL DEFAULT '{}',
    draft_result_json TEXT,
    user_key         TEXT,
    chat_key         TEXT,
    session_id       TEXT,
    created_at_ts    REAL NOT NULL,
    updated_at_ts    REAL NOT NULL,
    timeout_at_ts    REAL
);
"""

_SQLITE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_cp_wizard_sessions_session "
    "ON cp_wizard_sessions(session_id);",
    "CREATE INDEX IF NOT EXISTS idx_cp_wizard_sessions_user "
    "ON cp_wizard_sessions(user_key, chat_key);",
    "CREATE INDEX IF NOT EXISTS idx_cp_wizard_sessions_state_timeout "
    "ON cp_wizard_sessions(state, timeout_at_ts);",
)

_SQLITE_COLUMNS = (
    "wizard_id",
    "command_name",
    "state",
    "step",
    "total_steps",
    "session_data_json",
    "draft_result_json",
    "user_key",
    "chat_key",
    "session_id",
    "created_at_ts",
    "updated_at_ts",
    "timeout_at_ts",
)


def _dt_to_ts(value: Optional[datetime]) -> Optional[float]:
    """Convert a datetime to a UTC unix epoch float (None passes through)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _ts_to_dt(value: Optional[float]) -> Optional[datetime]:
    """Convert a UTC unix epoch float back to a timezone-aware datetime."""
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _row_to_session(row: sqlite3.Row) -> WizardSession:
    session_data = (
        json.loads(row["session_data_json"]) if row["session_data_json"] else {}
    )
    draft_raw = row["draft_result_json"]
    draft_result = json.loads(draft_raw) if draft_raw is not None else None
    return WizardSession(
        wizard_id=row["wizard_id"],
        command_name=row["command_name"],
        state=WizardState(row["state"]),
        step=int(row["step"]),
        total_steps=int(row["total_steps"]),
        session_data=session_data,
        created_at=_ts_to_dt(row["created_at_ts"]),
        updated_at=_ts_to_dt(row["updated_at_ts"]),
        user_key=row["user_key"],
        chat_key=row["chat_key"],
        session_id=row["session_id"],
        draft_result=draft_result,
        timeout_at=_ts_to_dt(row["timeout_at_ts"]),
    )


def _session_to_params(session: WizardSession) -> tuple:
    return (
        session.wizard_id,
        session.command_name,
        session.state.value,
        int(session.step),
        int(session.total_steps),
        json.dumps(session.session_data or {}),
        json.dumps(session.draft_result) if session.draft_result is not None else None,
        session.user_key,
        session.chat_key,
        session.session_id,
        _dt_to_ts(session.created_at),
        _dt_to_ts(session.updated_at),
        _dt_to_ts(session.timeout_at),
    )


class SqliteWizardStore(WizardStore):
    """SQLite-backed wizard session storage that mirrors :class:`InMemoryWizardStore`."""

    def __init__(self, sqlite_path: Union[str, Path]):
        self._path = str(sqlite_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._closed = False
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.execute(_SQLITE_SCHEMA)
            for ddl in _SQLITE_INDEXES:
                self._conn.execute(ddl)
            self._conn.commit()

    async def init(self):
        """Schema is created in __init__; this is a no-op for API parity."""
        return None

    async def _get_raw_session(self, wizard_id: str) -> Optional[WizardSession]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM cp_wizard_sessions WHERE wizard_id = ?",
                (wizard_id,),
            )
            row = cur.fetchone()
        return _row_to_session(row) if row is not None else None

    async def _save_raw_session(self, session: WizardSession) -> bool:
        params = _session_to_params(session)
        cols = ", ".join(_SQLITE_COLUMNS)
        placeholders = ", ".join("?" for _ in _SQLITE_COLUMNS)
        update_clause = ", ".join(
            f"{col}=excluded.{col}" for col in _SQLITE_COLUMNS if col != "wizard_id"
        )
        sql = (
            f"INSERT INTO cp_wizard_sessions ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(wizard_id) DO UPDATE SET {update_clause}"
        )
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()
        return True

    async def _get_raw_sessions_by_user(self, user_key: str) -> List[WizardSession]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM cp_wizard_sessions WHERE user_key = ?",
                (user_key,),
            )
            rows = cur.fetchall()
        return [_row_to_session(row) for row in rows]

    async def _get_raw_sessions_by_chat(self, chat_key: str) -> List[WizardSession]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM cp_wizard_sessions WHERE chat_key = ?",
                (chat_key,),
            )
            rows = cur.fetchall()
        return [_row_to_session(row) for row in rows]

    async def delete_session(self, wizard_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM cp_wizard_sessions WHERE wizard_id = ?",
                (wizard_id,),
            )
            self._conn.commit()
            deleted = cur.rowcount > 0
        return deleted

    async def expire_overdue(self) -> int:
        """Flip ACTIVE rows whose timeout has elapsed to TIMEOUT.

        Returns the number of rows transitioned. Rows with NULL
        ``timeout_at_ts`` (no expiry) are left untouched.
        """
        now_ts = datetime.now(timezone.utc).timestamp()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE cp_wizard_sessions "
                "SET state = ?, updated_at_ts = ? "
                "WHERE state = ? "
                "AND timeout_at_ts IS NOT NULL "
                "AND timeout_at_ts < ?",
                (
                    WizardState.TIMEOUT.value,
                    now_ts,
                    WizardState.ACTIVE.value,
                    now_ts,
                ),
            )
            self._conn.commit()
            flipped = cur.rowcount
        return int(flipped)

    async def close(self):
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True


class StoreFactory:
    """Factory for creating different types of wizard stores."""

    @staticmethod
    def create_store(kind: str = "in_memory", **kwargs) -> WizardStore:
        """Create a store instance based on the specified kind."""
        if kind == "in_memory":
            return InMemoryWizardStore()
        if kind == "sqlite":
            sqlite_path = kwargs.get("sqlite_path")
            if not sqlite_path:
                raise ValueError("sqlite kind requires sqlite_path keyword arg")
            return SqliteWizardStore(sqlite_path)
        raise ValueError(f"Unknown store type: {kind}")


_STORE_REGISTRY: Dict[str, WizardStore] = {}


def register_store(kind: str, store: WizardStore) -> None:
    """Explicitly register a constructed store instance under ``kind``."""
    _STORE_REGISTRY[kind] = store


async def get_wizard_store(kind: str = "in_memory", **kwargs) -> WizardStore:
    """Get a wizard store instance (potentially as a singleton)."""
    if kind == "in_memory" and "sqlite" in _STORE_REGISTRY:
        return _STORE_REGISTRY["sqlite"]
    if kind not in _STORE_REGISTRY:
        store = StoreFactory.create_store(kind, **kwargs)
        await store.init()
        _STORE_REGISTRY[kind] = store
    return _STORE_REGISTRY[kind]


async def close_all_stores():
    """Close and cleanup all registered stores."""
    for store in _STORE_REGISTRY.values():
        await store.close()
    _STORE_REGISTRY.clear()
