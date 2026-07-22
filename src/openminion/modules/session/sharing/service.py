"""Opt-in read-only session share service."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from collections.abc import Mapping
from uuid import uuid4

from openminion.modules.storage.record_store import RecordStore
from openminion.modules.session.interfaces import SESSION_SHARE_PROJECTION_VERSION

from .schemas import (
    SESSION_SHARE_SCHEMA_VERSION,
    SESSION_SHARE_TOKEN_BYTES,
    SessionShareCreationResult,
    SessionShareDeniedError,
    SessionShareError,
    SessionShareExpiredError,
    SessionShareNotFoundError,
    SessionShareRateLimitedError,
    SessionShareRecordV1,
    SessionShareRevokedError,
    SessionShareTokenTransportError,
)

_SESSION_SHARE_EVENTS = {
    "created": "session.share.created",
    "accessed": "session.share.accessed",
    "denied": "session.share.denied",
    "revoked": "session.share.revoked",
    "expired": "session.share.expired",
}
_FORBIDDEN_QUERY_KEYS = {"token", "share_token", "access_token"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_dumps(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=True, sort_keys=True)


def _json_loads(value: object, fallback: Any) -> Any:
    try:
        return json.loads(str(value)) if value not in {None, ""} else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def generate_share_token() -> str:
    return secrets.token_urlsafe(SESSION_SHARE_TOKEN_BYTES).rstrip("=")


def hash_share_token(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def verify_share_token(*, token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_share_token(token), str(expected_hash))


def reject_forbidden_token_transport(
    *, query_args: Mapping[str, Any] | None, cookies: str | None = None
) -> None:
    keys = {str(key).lower() for key in dict(query_args or {})}
    if keys & _FORBIDDEN_QUERY_KEYS:
        raise SessionShareTokenTransportError("share tokens must use Authorization: Bearer")
    if "share_token" in str(cookies or "").lower():
        raise SessionShareTokenTransportError("share tokens are forbidden in cookies")


def extract_bearer_token(headers: Mapping[str, str] | None) -> str:
    raw = ""
    for key, value in dict(headers or {}).items():
        if key.lower() == "authorization":
            raw = str(value)
            break
    prefix = "Bearer "
    token = raw[len(prefix) :].strip() if raw.startswith(prefix) else ""
    if not token:
        raise SessionShareDeniedError("Authorization: Bearer token is required")
    return token


@dataclass
class SessionShareService:
    store: Any
    rate_limit: int = 8
    window_seconds: int = 60
    _attempts: dict[str, list[datetime]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._record_store = self._resolve_record_store(self.store)
        self._ensure_schema()

    @staticmethod
    def _resolve_record_store(store: Any) -> RecordStore:
        record_store = getattr(store, "_record_store", None)
        if record_store is None:
            raise TypeError("session sharing requires a session store with a record store")
        return record_store

    def create_share(
        self,
        *,
        session_id: str,
        created_by: str,
        ttl_seconds: int = 3600,
        projection_policy: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> SessionShareCreationResult:
        self._require_session(session_id)
        token = generate_share_token()
        current = now or _utc_now()
        record = SessionShareRecordV1(
            share_id=f"share-{uuid4().hex[:16]}",
            session_id=str(session_id),
            token_hash=hash_share_token(token),
            token_hint=token[-8:],
            created_by=str(created_by or "operator"),
            created_at=_to_iso(current),
            expires_at=_to_iso(current + timedelta(seconds=max(1, int(ttl_seconds)))),
            projection_policy=dict(projection_policy or {"mode": "structural_read_only"}),
            meta={"transport": "authorization_bearer"},
        )
        self._record_store.insert(
            "session_shares",
            {
                "share_id": record.share_id,
                "session_id": record.session_id,
                "token_hash": record.token_hash,
                "token_hint": record.token_hint,
                "created_by": record.created_by,
                "created_at": record.created_at,
                "expires_at": record.expires_at,
                "revoked_at": record.revoked_at,
                "projection_policy_json": _json_dumps(record.projection_policy),
                "meta_json": _json_dumps(record.meta),
                "schema_version": record.schema_version,
            },
        )
        self._audit(record.session_id, _SESSION_SHARE_EVENTS["created"], record)
        return SessionShareCreationResult(record=record, token=token)

    def access_share(
        self,
        *,
        share_id: str,
        token: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        record = self.get_share(share_id)
        if record is None:
            raise SessionShareNotFoundError("session share not found")
        self._enforce_rate_limit(record.token_hash, now=now)
        if not verify_share_token(token=token, expected_hash=record.token_hash):
            self._audit(record.session_id, _SESSION_SHARE_EVENTS["denied"], record)
            raise SessionShareDeniedError("share token is invalid")
        self._assert_share_active(record, now=now)
        projection = self._build_projection(record)
        self._audit(record.session_id, _SESSION_SHARE_EVENTS["accessed"], record)
        return projection

    def revoke_share(self, share_id: str, *, now: datetime | None = None) -> SessionShareRecordV1:
        record = self.get_share(share_id)
        if record is None:
            raise SessionShareNotFoundError("session share not found")
        revoked_at = _to_iso(now or _utc_now())
        self._record_store.update_rows(
            "session_shares", {"share_id": record.share_id}, {"revoked_at": revoked_at}
        )
        revoked = self.get_share(share_id)
        assert revoked is not None
        self._audit(revoked.session_id, _SESSION_SHARE_EVENTS["revoked"], revoked)
        return revoked

    def get_share(self, share_id: str) -> SessionShareRecordV1 | None:
        rows = self._record_store.query_rows(
            "session_shares", where={"share_id": str(share_id)}, limit=1
        )
        return _record_from_row(rows[0]) if rows else None

    def list_shares(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._record_store.query_rows(
            "session_shares", where={"session_id": str(session_id)}, order="created_at DESC"
        )
        return [_record_from_row(row).public_dict() for row in rows]

    def access_count(self, share_id: str) -> int:
        record = self.get_share(share_id)
        if record is None:
            return 0
        rows = self._record_store.query_dicts(
            """
            SELECT COUNT(*) AS count
            FROM session_events
            WHERE session_id = ? AND event_type = ?
              AND payload_json LIKE ?
            """,
            (record.session_id, _SESSION_SHARE_EVENTS["accessed"], f'%"share_id":"{share_id}"%'),
        )
        return int(rows[0]["count"]) if rows else 0

    def _assert_share_active(
        self, record: SessionShareRecordV1, *, now: datetime | None = None
    ) -> None:
        if record.revoked_at:
            self._audit(record.session_id, _SESSION_SHARE_EVENTS["denied"], record)
            raise SessionShareRevokedError("session share is revoked")
        if _parse_iso(record.expires_at) <= (now or _utc_now()):
            self._audit(record.session_id, _SESSION_SHARE_EVENTS["expired"], record)
            raise SessionShareExpiredError("session share is expired")

    def _build_projection(self, record: SessionShareRecordV1) -> dict[str, Any]:
        turns = self.store.list_turns(record.session_id, limit=50)
        events = self.store.get_events(record.session_id, limit=100)
        session = self.store.get_session(record.session_id) or {}
        return {
            "schema_version": SESSION_SHARE_PROJECTION_VERSION,
            "share": record.public_dict(),
            "session": {
                "session_id": record.session_id,
                "title": session.get("title"),
                "status": session.get("status"),
            },
            "turns": [_project_turn(turn) for turn in turns],
            "events": [_project_event(event) for event in events],
            "readonly": True,
        }

    def _audit(
        self,
        session_id: str,
        event_type: str,
        record: SessionShareRecordV1,
    ) -> None:
        self.store.append_event(
            session_id,
            event_type=event_type,
            payload={
                "share_id": record.share_id,
                "token_hint": record.token_hint,
                "schema_version": SESSION_SHARE_SCHEMA_VERSION,
            },
            actor_type="system",
            redaction="none",
        )

    def _enforce_rate_limit(self, token_hash: str, *, now: datetime | None = None) -> None:
        current = now or _utc_now()
        cutoff = current - timedelta(seconds=max(1, int(self.window_seconds)))
        attempts = [item for item in self._attempts.get(token_hash, []) if item > cutoff]
        attempts.append(current)
        self._attempts[token_hash] = attempts
        if len(attempts) > max(1, int(self.rate_limit)):
            raise SessionShareRateLimitedError("too many share access attempts")

    def _ensure_schema(self) -> None:
        for statement in SESSION_SHARING_SCHEMA:
            self._record_store.execute_count(statement)

    def _require_session(self, session_id: str) -> None:
        if self.store.get_session(session_id) is None:
            raise SessionShareError("session not found", details={"session_id": session_id})


SESSION_SHARING_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS session_shares (
      share_id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      token_hash TEXT NOT NULL UNIQUE,
      token_hint TEXT NOT NULL,
      created_by TEXT NOT NULL,
      created_at TEXT NOT NULL,
      expires_at TEXT NOT NULL,
      revoked_at TEXT,
      projection_policy_json TEXT NOT NULL DEFAULT '{}',
      meta_json TEXT NOT NULL DEFAULT '{}',
      schema_version TEXT NOT NULL DEFAULT 'session_share.v1',
      FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_shares_session
    ON session_shares(session_id, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_shares_expiry
    ON session_shares(expires_at)
    """,
)


def _record_from_row(row: Mapping[str, Any]) -> SessionShareRecordV1:
    return SessionShareRecordV1(
        share_id=str(row["share_id"]),
        session_id=str(row["session_id"]),
        token_hash=str(row["token_hash"]),
        token_hint=str(row["token_hint"]),
        created_by=str(row["created_by"]),
        created_at=str(row["created_at"]),
        expires_at=str(row["expires_at"]),
        revoked_at=str(row["revoked_at"]) if row.get("revoked_at") else None,
        projection_policy=dict(_json_loads(row.get("projection_policy_json"), {})),
        meta=dict(_json_loads(row.get("meta_json"), {})),
        schema_version=str(row.get("schema_version") or SESSION_SHARE_SCHEMA_VERSION),
    )


def _project_turn(turn: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "turn_id": turn.get("turn_id"),
        "role": turn.get("role"),
        "text": turn.get("content"),
        "ts": turn.get("ts"),
    }


def _project_event(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event.get("event_id"),
        "seq": event.get("seq"),
        "timestamp": event.get("timestamp"),
        "event_type": event.get("event_type"),
        "actor_type": event.get("actor_type"),
    }
