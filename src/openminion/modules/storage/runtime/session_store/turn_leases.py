from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .backend import RuntimeSessionStoreBackend


@dataclass(frozen=True)
class RuntimeSessionTurnLease:
    session_id: str
    owner: str
    request_id: str
    fence_token: int
    acquired_at: str
    renewed_at: str
    expires_at: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "RuntimeSessionTurnLease":
        return cls(
            session_id=str(row["session_id"]),
            owner=str(row["owner"]),
            request_id=str(row["request_id"]),
            fence_token=int(row["fence_token"]),
            acquired_at=str(row["acquired_at"]),
            renewed_at=str(row["renewed_at"]),
            expires_at=str(row["expires_at"]),
        )


class RuntimeSessionTurnBusyError(RuntimeError):
    code = "SESSION_TURN_BUSY"

    def __init__(
        self,
        session_id: str,
        *,
        retry_after_s: int,
        active_owner: str = "",
        expires_at: str = "",
    ) -> None:
        self.session_id = session_id
        self.retry_after_s = max(1, int(retry_after_s))
        self.active_owner = active_owner
        self.expires_at = expires_at
        super().__init__(
            f"session {session_id!r} already has an active turn; "
            f"retry after {self.retry_after_s}s"
        )


class RuntimeSessionTurnFenceError(RuntimeError):
    code = "SESSION_TURN_FENCE_STALE"


def _parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _lease_expiry(now_iso: str | None, ttl_s: int) -> tuple[str, str]:
    now_dt = _parse_iso_datetime(now_iso) if now_iso else _utc_now()
    now = _to_iso_utc(now_dt)
    expires = _to_iso_utc(now_dt + timedelta(seconds=max(1, int(ttl_s))))
    return now, expires


def _retry_after_seconds(expires_at: str, now_iso: str | None) -> int:
    try:
        expires_dt = _parse_iso_datetime(expires_at)
        now_dt = _parse_iso_datetime(now_iso) if now_iso else _utc_now()
    except (TypeError, ValueError, OverflowError):
        return 1
    return max(1, int((expires_dt - now_dt).total_seconds()))


class RuntimeSessionTurnLeases:
    def __init__(self, backend: RuntimeSessionStoreBackend) -> None:
        self._backend = backend

    def acquire(
        self,
        session_id: str,
        *,
        owner: str,
        request_id: str,
        ttl_s: int = 60,
        now_iso: str | None = None,
    ) -> RuntimeSessionTurnLease:
        sid = str(session_id or "").strip()
        lease_owner = str(owner or "").strip()
        request = str(request_id or "").strip()
        if not sid:
            raise ValueError("session_id is required")
        if not lease_owner:
            raise ValueError("owner is required")
        if not request:
            raise ValueError("request_id is required")
        now, expires = _lease_expiry(now_iso, ttl_s)
        with self._backend.transaction():
            current = self._active_row(session_id=sid, now_iso=now)
            if current is not None:
                if (
                    str(current["owner"]) == lease_owner
                    and str(current["request_id"]) == request
                ):
                    self._renew_locked(
                        session_id=sid,
                        owner=lease_owner,
                        fence_token=int(current["fence_token"]),
                        now_iso=now,
                        expires_at=expires,
                    )
                    refreshed = self._row(session_id=sid)
                    if refreshed is not None:
                        return RuntimeSessionTurnLease.from_row(refreshed)
                raise RuntimeSessionTurnBusyError(
                    sid,
                    retry_after_s=_retry_after_seconds(str(current["expires_at"]), now),
                    active_owner=str(current["owner"]),
                    expires_at=str(current["expires_at"]),
                )
            prior = self._backend.query_one(
                """
                SELECT COALESCE(MAX(fence_token), 0) AS max_fence
                FROM session_turn_leases
                WHERE session_id = ?
                """,
                (sid,),
            )
            next_fence = int(prior["max_fence"]) + 1 if prior is not None else 1
            self._backend.execute_count(
                """
                INSERT INTO session_turn_leases(
                  session_id, owner, request_id, fence_token,
                  acquired_at, renewed_at, expires_at, released_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(session_id) DO UPDATE SET
                  owner = excluded.owner,
                  request_id = excluded.request_id,
                  fence_token = excluded.fence_token,
                  acquired_at = excluded.acquired_at,
                  renewed_at = excluded.renewed_at,
                  expires_at = excluded.expires_at,
                  released_at = NULL
                """,
                (sid, lease_owner, request, next_fence, now, now, expires),
            )
            row = self._row(session_id=sid)
            if row is None:  # pragma: no cover - defensive storage invariant
                raise RuntimeError("failed to acquire session turn lease")
            return RuntimeSessionTurnLease.from_row(row)

    def renew(
        self,
        session_id: str,
        *,
        owner: str,
        fence_token: int,
        ttl_s: int = 60,
        now_iso: str | None = None,
    ) -> bool:
        now, expires = _lease_expiry(now_iso, ttl_s)
        with self._backend.transaction():
            return self._renew_locked(
                session_id=str(session_id or "").strip(),
                owner=str(owner or "").strip(),
                fence_token=int(fence_token),
                now_iso=now,
                expires_at=expires,
            )

    def release(
        self,
        session_id: str,
        *,
        owner: str,
        fence_token: int,
        now_iso: str | None = None,
    ) -> bool:
        sid = str(session_id or "").strip()
        lease_owner = str(owner or "").strip()
        now, _expires = _lease_expiry(now_iso, 1)
        with self._backend.transaction():
            return (
                self._backend.execute_count(
                    """
                    UPDATE session_turn_leases
                    SET released_at = ?, renewed_at = ?
                    WHERE session_id = ?
                      AND owner = ?
                      AND fence_token = ?
                      AND released_at IS NULL
                    """,
                    (now, now, sid, lease_owner, int(fence_token)),
                )
                > 0
            )

    def assert_fence(self, session_id: str, *, fence_token: int) -> None:
        row = self._row(session_id=str(session_id or "").strip())
        if row is None or row["released_at"] is not None:
            raise RuntimeSessionTurnFenceError("session turn lease is not active")
        if int(row["fence_token"]) != int(fence_token):
            raise RuntimeSessionTurnFenceError("stale session turn fence")

    def _row(self, *, session_id: str) -> dict[str, Any] | None:
        return self._backend.query_one(
            """
            SELECT *
            FROM session_turn_leases
            WHERE session_id = ?
            """,
            (session_id,),
        )

    def _active_row(
        self,
        *,
        session_id: str,
        now_iso: str,
    ) -> dict[str, Any] | None:
        return self._backend.query_one(
            """
            SELECT *
            FROM session_turn_leases
            WHERE session_id = ?
              AND released_at IS NULL
              AND expires_at > ?
            """,
            (session_id, now_iso),
        )

    def _renew_locked(
        self,
        *,
        session_id: str,
        owner: str,
        fence_token: int,
        now_iso: str,
        expires_at: str,
    ) -> bool:
        if not session_id or not owner:
            return False
        return (
            self._backend.execute_count(
                """
                UPDATE session_turn_leases
                SET renewed_at = ?, expires_at = ?
                WHERE session_id = ?
                  AND owner = ?
                  AND fence_token = ?
                  AND released_at IS NULL
                """,
                (now_iso, expires_at, session_id, owner, int(fence_token)),
            )
            > 0
        )
