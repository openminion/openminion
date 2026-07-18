import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime

from openminion.base.time import utc_now_iso as _now_iso
from openminion.modules.controlplane.channels.telegram.interfaces import (
    TELEGRAM_INTERFACE_VERSION,
)
from openminion.modules.controlplane.channels.telegram.models import (
    PairConsumeResult,
    PairTokenIssue,
)
from openminion.modules.storage.runtime.module_store import BaseModuleSQLiteStore
from .base import TelegramPollStateStoreBase
from .migrations import list_migrations

_TOKEN_ALLOWED_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _now_ts() -> int:
    return int(datetime.fromisoformat(_now_iso()).timestamp())


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


_PROCESS_START_MARKER = f"{os.getpid()}:{time.monotonic_ns()}"


@dataclass(frozen=True)
class PollingLease:
    acquired: bool
    account_id: str
    owner_pid: int | None = None
    owner_command: str | None = None
    reason: str | None = None

    def diagnostic(self) -> str:
        if self.acquired:
            return "telegram polling lease acquired"
        owner = f"pid={self.owner_pid}" if self.owner_pid else "pid=unknown"
        command = self.owner_command or "unknown"
        return (
            "Telegram polling is already owned locally "
            f"({owner}, command={command}). "
            "Stop that runner or use `openminion channel telegram status`."
        )


def _token_hash(token: str, *, pepper: str | None) -> str:
    material = f"{pepper or ''}{token}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _json_list_dump(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=True)


def _json_list_load(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [text for item in parsed if (text := str(item).strip())]


class TelegramPollStateStore(BaseModuleSQLiteStore, TelegramPollStateStoreBase):
    contract_version = TELEGRAM_INTERFACE_VERSION

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS telegram_poll_state (
                    account_id TEXT PRIMARY KEY,
                    last_update_id INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS telegram_pair_tokens (
                    token_hash TEXT PRIMARY KEY,
                    token_hint TEXT NOT NULL,
                    created_at_ts INTEGER NOT NULL,
                    expires_at_ts INTEGER NOT NULL,
                    used_at_ts INTEGER,
                    expected_user_id INTEGER,
                    expected_chat_id INTEGER,
                    scopes_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_telegram_pair_tokens_expiry
                    ON telegram_pair_tokens(expires_at_ts);

                CREATE TABLE IF NOT EXISTS telegram_pair_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempted_at_ts INTEGER NOT NULL,
                    token_hash_prefix TEXT NOT NULL,
                    user_id INTEGER,
                    chat_id INTEGER,
                    outcome TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_telegram_pair_attempts_ts
                    ON telegram_pair_attempts(attempted_at_ts);
                CREATE INDEX IF NOT EXISTS idx_telegram_pair_attempts_user_ts
                    ON telegram_pair_attempts(user_id, attempted_at_ts);
                CREATE INDEX IF NOT EXISTS idx_telegram_pair_attempts_chat_ts
                    ON telegram_pair_attempts(chat_id, attempted_at_ts);

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
                );

                CREATE TABLE IF NOT EXISTS telegram_polling_leases (
                    account_id TEXT PRIMARY KEY,
                    owner_pid INTEGER NOT NULL,
                    process_start_marker TEXT NOT NULL,
                    command TEXT NOT NULL,
                    acquired_at_ts INTEGER NOT NULL,
                    heartbeat_at_ts INTEGER NOT NULL
                );
                """
            )

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__

    def get_last_update_id(self, account_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT last_update_id FROM telegram_poll_state WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        if row is None:
            return 0
        try:
            return int(row["last_update_id"])
        except (TypeError, ValueError):
            return 0

    def set_last_update_id(self, account_id: str, update_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO telegram_poll_state(account_id, last_update_id, updated_at)
                VALUES (?,?,?)
                ON CONFLICT(account_id) DO UPDATE SET
                    last_update_id=excluded.last_update_id,
                    updated_at=excluded.updated_at
                """,
                (account_id, int(update_id), _now_iso()),
            )

    def acquire_polling_lease(
        self,
        *,
        account_id: str,
        command: str,
        stale_after_seconds: int = 120,
    ) -> PollingLease:
        normalized_account = str(account_id or "").strip()
        if not normalized_account:
            raise ValueError("account_id is required for telegram polling lease")
        now_ts = _now_ts()
        pid = os.getpid()
        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT owner_pid, process_start_marker, command, heartbeat_at_ts
                FROM telegram_polling_leases
                WHERE account_id = ?
                """,
                (normalized_account,),
            ).fetchone()
            if row is not None:
                owner_pid = int(row["owner_pid"])
                owner_marker = str(row["process_start_marker"] or "")
                heartbeat_at = int(row["heartbeat_at_ts"] or 0)
                owned_by_this_process = (
                    owner_pid == pid and owner_marker == _PROCESS_START_MARKER
                )
                stale = now_ts - heartbeat_at > max(1, int(stale_after_seconds))
                if not owned_by_this_process and not stale and _pid_is_alive(owner_pid):
                    return PollingLease(
                        acquired=False,
                        account_id=normalized_account,
                        owner_pid=owner_pid,
                        owner_command=str(row["command"] or ""),
                        reason="live_owner",
                    )
            self._conn.execute(
                """
                INSERT INTO telegram_polling_leases(
                    account_id,
                    owner_pid,
                    process_start_marker,
                    command,
                    acquired_at_ts,
                    heartbeat_at_ts
                ) VALUES (?,?,?,?,?,?)
                ON CONFLICT(account_id) DO UPDATE SET
                    owner_pid=excluded.owner_pid,
                    process_start_marker=excluded.process_start_marker,
                    command=excluded.command,
                    acquired_at_ts=excluded.acquired_at_ts,
                    heartbeat_at_ts=excluded.heartbeat_at_ts
                """,
                (
                    normalized_account,
                    pid,
                    _PROCESS_START_MARKER,
                    str(command or "telegram").strip() or "telegram",
                    now_ts,
                    now_ts,
                ),
            )
        return PollingLease(acquired=True, account_id=normalized_account)

    def heartbeat_polling_lease(self, *, account_id: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                UPDATE telegram_polling_leases
                SET heartbeat_at_ts = ?
                WHERE account_id = ?
                  AND owner_pid = ?
                  AND process_start_marker = ?
                """,
                (_now_ts(), account_id, os.getpid(), _PROCESS_START_MARKER),
            )
        return cur.rowcount > 0

    def release_polling_lease(self, *, account_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                DELETE FROM telegram_polling_leases
                WHERE account_id = ?
                  AND owner_pid = ?
                  AND process_start_marker = ?
                """,
                (account_id, os.getpid(), _PROCESS_START_MARKER),
            )

    def issue_pair_token(
        self,
        *,
        token: str | None,
        token_ttl_seconds: int,
        scopes: list[str],
        expected_user_id: int | None,
        expected_chat_id: int | None,
        hash_pepper: str | None,
    ) -> PairTokenIssue:
        generated = token or secrets.token_urlsafe(24).rstrip("=")
        if not _TOKEN_ALLOWED_RE.fullmatch(generated):
            raise ValueError(
                "pair token must match Telegram start-parameter charset and length constraints"
            )
        token_hash = _token_hash(generated, pepper=hash_pepper)
        token_hint = generated[:4]
        now_ts = _now_ts()
        expires_at_ts = now_ts + max(60, int(token_ttl_seconds))

        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO telegram_pair_tokens(
                    token_hash,
                    token_hint,
                    created_at_ts,
                    expires_at_ts,
                    expected_user_id,
                    expected_chat_id,
                    scopes_json
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    token_hash,
                    token_hint,
                    now_ts,
                    expires_at_ts,
                    expected_user_id,
                    expected_chat_id,
                    _json_list_dump(scopes),
                ),
            )

        return PairTokenIssue(
            token=generated,
            token_hint=token_hint,
            token_hash_prefix=token_hash[:12],
            expires_at_ts=expires_at_ts,
            scopes=list(scopes),
        )

    def consume_pair_token(
        self,
        *,
        token: str,
        user_id: int,
        chat_id: int,
        topic_id: int | None,
        hash_pepper: str | None,
    ) -> PairConsumeResult:
        token_hash = _token_hash(token, pepper=hash_pepper)
        now_ts = _now_ts()

        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT token_hash, token_hint, expires_at_ts, used_at_ts,
                       expected_user_id, expected_chat_id, scopes_json
                FROM telegram_pair_tokens
                WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()

            if row is None:
                return PairConsumeResult(
                    ok=False,
                    reason="invalid_token",
                    token_hint=token[:4],
                    token_hash_prefix=token_hash[:12],
                )

            token_hint = str(row["token_hint"] or "")
            if row["used_at_ts"] is not None:
                return PairConsumeResult(
                    ok=False,
                    reason="already_used",
                    token_hint=token_hint,
                    token_hash_prefix=token_hash[:12],
                )

            try:
                expires_at_ts = int(row["expires_at_ts"])
            except (TypeError, ValueError):
                expires_at_ts = 0
            if expires_at_ts < now_ts:
                return PairConsumeResult(
                    ok=False,
                    reason="expired_token",
                    token_hint=token_hint,
                    token_hash_prefix=token_hash[:12],
                )

            expected_user_id = row["expected_user_id"]
            if expected_user_id is not None and int(expected_user_id) != int(user_id):
                return PairConsumeResult(
                    ok=False,
                    reason="user_mismatch",
                    token_hint=token_hint,
                    token_hash_prefix=token_hash[:12],
                )

            expected_chat_id = row["expected_chat_id"]
            if expected_chat_id is not None and int(expected_chat_id) != int(chat_id):
                return PairConsumeResult(
                    ok=False,
                    reason="chat_mismatch",
                    token_hint=token_hint,
                    token_hash_prefix=token_hash[:12],
                )

            cur = self._conn.execute(
                """
                UPDATE telegram_pair_tokens
                SET used_at_ts = ?
                WHERE token_hash = ?
                  AND used_at_ts IS NULL
                  AND expires_at_ts >= ?
                """,
                (now_ts, token_hash, now_ts),
            )
            if int(cur.rowcount or 0) != 1:
                return PairConsumeResult(
                    ok=False,
                    reason="token_race",
                    token_hint=token_hint,
                    token_hash_prefix=token_hash[:12],
                )

            scopes = _json_list_load(row["scopes_json"])

        return PairConsumeResult(
            ok=True,
            reason="paired",
            token_hint=str(row["token_hint"] or token[:4]),
            token_hash_prefix=token_hash[:12],
            scopes=scopes,
        )

    def record_pair_attempt(
        self,
        *,
        token: str,
        user_id: int,
        chat_id: int,
        outcome: str,
        hash_pepper: str | None,
    ) -> None:
        token_hash = _token_hash(token, pepper=hash_pepper)
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO telegram_pair_attempts(
                    attempted_at_ts,
                    token_hash_prefix,
                    user_id,
                    chat_id,
                    outcome
                ) VALUES (?,?,?,?,?)
                """,
                (_now_ts(), token_hash[:12], int(user_id), int(chat_id), str(outcome)),
            )

    def count_recent_attempts_for_user(
        self, *, user_id: int, window_seconds: int
    ) -> int:
        min_ts = _now_ts() - max(1, int(window_seconds))
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM telegram_pair_attempts
                WHERE user_id = ? AND attempted_at_ts >= ?
                """,
                (int(user_id), min_ts),
            ).fetchone()
        return int(row["cnt"] if row else 0)

    def count_recent_attempts_for_chat(
        self, *, chat_id: int, window_seconds: int
    ) -> int:
        min_ts = _now_ts() - max(1, int(window_seconds))
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM telegram_pair_attempts
                WHERE chat_id = ? AND attempted_at_ts >= ?
                """,
                (int(chat_id), min_ts),
            ).fetchone()
        return int(row["cnt"] if row else 0)
