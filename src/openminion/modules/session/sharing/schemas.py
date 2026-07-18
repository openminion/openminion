"""Versioned schemas for opt-in session sharing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openminion.modules.session.interfaces import SESSION_SHARE_SCHEMA_VERSION

SESSION_SHARE_TOKEN_BYTES = 32


@dataclass(frozen=True)
class SessionShareRecordV1:
    share_id: str
    session_id: str
    token_hash: str
    token_hint: str
    created_by: str
    created_at: str
    expires_at: str
    revoked_at: str | None = None
    projection_policy: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SESSION_SHARE_SCHEMA_VERSION

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "share_id": self.share_id,
            "session_id": self.session_id,
            "token_hint": self.token_hint,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "projection_policy": dict(self.projection_policy),
            "meta": dict(self.meta),
        }


@dataclass(frozen=True)
class SessionShareCreationResult:
    record: SessionShareRecordV1
    token: str

    def response_payload(self) -> dict[str, Any]:
        payload = self.record.public_dict()
        payload["token"] = self.token
        payload["token_return_policy"] = "returned_once_at_creation"
        return payload


class SessionShareError(RuntimeError):
    code = "SESSION_SHARE_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        self.details = dict(details or {})
        super().__init__(message)


class SessionShareDeniedError(SessionShareError):
    code = "SESSION_SHARE_DENIED"


class SessionShareExpiredError(SessionShareError):
    code = "SESSION_SHARE_EXPIRED"


class SessionShareRevokedError(SessionShareError):
    code = "SESSION_SHARE_REVOKED"


class SessionShareTokenTransportError(SessionShareError):
    code = "SESSION_SHARE_TOKEN_TRANSPORT_FORBIDDEN"


class SessionShareNotFoundError(SessionShareError):
    code = "SESSION_SHARE_NOT_FOUND"


class SessionShareRateLimitedError(SessionShareError):
    code = "SESSION_SHARE_RATE_LIMITED"
