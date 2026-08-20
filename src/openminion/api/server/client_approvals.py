"""Lease-bound desktop approval waits and bounded terminal tombstones."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
import secrets
from threading import Event, RLock
from typing import Any

from openminion.api.server.client_auth import ClientAuthService, ClientIdentity
from openminion.modules.telemetry.events.catalog import (
    DESKTOP_APPROVAL_REQUESTED,
    DESKTOP_APPROVAL_RESOLVED,
)
from openminion.services.runtime.manager import (
    DesktopApprovalRequest,
    DesktopApprovalRequester,
    TurnChunk,
)


_APPROVAL_TTL_SECONDS = 60
_TOMBSTONE_TTL_SECONDS = 300
_TOMBSTONE_LIMIT = 512
_KEY_LIMIT = 64
_KEY_BYTES = 256
_KEY_ARRAY_BYTES = 8 * 1024


class ClientApprovalError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _ApprovalRecord:
    client_id: str
    session_id: str
    trace_id: str
    approval_id: str
    call_id: str
    tool_name: str
    argument_keys: tuple[str, ...]
    requested_at: str
    expires_at: str
    state: str = "pending"
    decision: str | None = None
    resolved_at: str | None = None
    wait_event: Event = field(default_factory=Event)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.client_id, self.session_id, self.trace_id, self.approval_id


class ClientApprovalCoordinator:
    """Own active desktop waits without becoming policy or durable truth."""

    def __init__(self, *, client_auth: ClientAuthService, runtime: Any) -> None:
        self._client_auth = client_auth
        self._runtime = runtime
        self._active: dict[tuple[str, str, str, str], _ApprovalRecord] = {}
        self._tombstones: OrderedDict[tuple[str, str, str, str], _ApprovalRecord] = (
            OrderedDict()
        )
        self._closed_sessions: set[str] = set()
        self._lock = RLock()
        self._closed = False

    def bind(self, identity: ClientIdentity) -> DesktopApprovalRequester:
        def request_approval(request: DesktopApprovalRequest) -> bool:
            return self.request(identity, request)

        return request_approval

    def request(
        self,
        identity: ClientIdentity,
        request: DesktopApprovalRequest,
    ) -> bool:
        if not self._valid_request(request) or not self._client_auth.is_active(
            identity
        ):
            return False
        with self._lock:
            if self._closed or request.session_id in self._closed_sessions:
                return False
        now = datetime.now(UTC)
        deadline = now + timedelta(seconds=_APPROVAL_TTL_SECONDS)
        record = _ApprovalRecord(
            client_id=identity.client_id,
            session_id=request.session_id,
            trace_id=request.trace_id,
            approval_id=secrets.token_urlsafe(24),
            call_id=request.call_id,
            tool_name=request.tool_name,
            argument_keys=request.argument_keys,
            requested_at=_timestamp(now),
            expires_at=_timestamp(deadline),
        )
        if not self._append_requested(record):
            return False
        with self._lock:
            self._purge_tombstones_locked()
            self._active[record.key] = record
            closed = self._closed
            session_closed = record.session_id in self._closed_sessions
            lease_active = self._client_auth.is_active(identity)
        if closed or session_closed or not lease_active:
            self._resolve_automatic(
                record.key,
                "interrupted"
                if closed
                else "cancelled"
                if session_closed
                else "expired",
            )
            return False
        try:
            request.emit_chunk(
                TurnChunk(
                    trace_id=record.trace_id,
                    kind="approval_required",
                    data={
                        "approval_id": record.approval_id,
                        "call_id": record.call_id,
                        "tool_name": record.tool_name,
                        "argument_keys": list(record.argument_keys),
                        "requested_at": record.requested_at,
                        "expires_at": record.expires_at,
                    },
                )
            )
        except Exception:
            self._resolve_automatic(record.key, "interrupted")
            return False

        while not record.wait_event.wait(timeout=0.1):
            if request.cancel_event.is_set():
                self._resolve_automatic(record.key, "cancelled")
            elif not self._client_auth.is_active(identity):
                self._resolve_automatic(record.key, "expired")
            elif _is_expired(record.expires_at):
                self._resolve_automatic(record.key, "expired")
        if record.state != "event_failed":
            try:
                request.emit_chunk(
                    TurnChunk(
                        trace_id=record.trace_id,
                        kind="approval_resolved",
                        data={
                            "approval_id": record.approval_id,
                            "call_id": record.call_id,
                            "tool_name": record.tool_name,
                            "decision": record.decision,
                            "outcome": record.state,
                            "resolved_at": record.resolved_at,
                        },
                    )
                )
            except Exception:
                pass
        return record.state == "applied"

    def decide(
        self,
        identity: ClientIdentity,
        *,
        session_id: str,
        trace_id: str,
        approval_id: str,
        decision: str,
    ) -> dict[str, Any]:
        key = identity.client_id, session_id, trace_id, approval_id
        with self._lock:
            self._purge_tombstones_locked()
            record = self._active.get(key)
            if record is None:
                return self._repeat_result_locked(key, decision)
            if not self._client_auth.is_active(identity):
                self._resolve_locked(record, decision=None, outcome="expired")
                raise _approval_error("approval_expired")
            if _is_expired(record.expires_at):
                self._resolve_locked(record, decision=None, outcome="expired")
                raise _approval_error("approval_expired")
            outcome = "applied" if decision == "allow_once" else "denied"
            if not self._resolve_locked(record, decision=decision, outcome=outcome):
                raise _approval_error("approval_event_failed")
            return self._decision_payload(record, outcome)

    def cancel_client(self, client_id: str, _reason: str) -> None:
        self._cancel_matching(lambda record: record.client_id == client_id, "cancelled")

    def cancel_session(self, session_id: str, _reason: str) -> None:
        with self._lock:
            self._closed_sessions.add(session_id)
        self._cancel_matching(
            lambda record: record.session_id == session_id, "cancelled"
        )

    def cancel_trace(
        self,
        client_id: str,
        session_id: str,
        trace_id: str,
        _reason: str,
    ) -> None:
        self._cancel_matching(
            lambda record: (
                record.client_id == client_id
                and record.session_id == session_id
                and record.trace_id == trace_id
            ),
            "cancelled",
        )

    def close(self, _reason: str = "shutdown") -> None:
        with self._lock:
            self._closed = True
        self._cancel_matching(lambda _record: True, "interrupted")

    def recovery_outcome(
        self,
        client_id: str,
        session_id: str,
        trace_id: str,
        approval_id: str,
        expires_at: str,
    ) -> str:
        key = client_id, session_id, trace_id, approval_id
        with self._lock:
            self._purge_tombstones_locked()
            if key in self._active:
                return "pending"
            if record := self._tombstones.get(key):
                return record.state
        expires = _parse_timestamp(expires_at)
        return (
            "expired"
            if expires is not None and expires <= datetime.now(UTC)
            else "interrupted"
        )

    def _cancel_matching(self, matches: Any, outcome: str) -> None:
        with self._lock:
            keys = [record.key for record in self._active.values() if matches(record)]
        for key in keys:
            self._resolve_automatic(key, outcome)

    def _resolve_automatic(
        self,
        key: tuple[str, str, str, str],
        outcome: str,
    ) -> None:
        with self._lock:
            if record := self._active.get(key):
                self._resolve_locked(record, decision=None, outcome=outcome)

    def _resolve_locked(
        self,
        record: _ApprovalRecord,
        *,
        decision: str | None,
        outcome: str,
    ) -> bool:
        if record.key not in self._active:
            return record.state != "event_failed"
        resolved_at = _timestamp(datetime.now(UTC))
        persisted = self._append_resolved(
            record,
            decision=decision,
            outcome=outcome,
            resolved_at=resolved_at,
        )
        record.decision = decision if persisted else None
        record.state = outcome if persisted else "event_failed"
        record.resolved_at = resolved_at
        self._active.pop(record.key, None)
        self._tombstones[record.key] = record
        self._purge_tombstones_locked()
        record.wait_event.set()
        return persisted

    def _repeat_result_locked(
        self,
        key: tuple[str, str, str, str],
        decision: str,
    ) -> dict[str, Any]:
        record = self._tombstones.get(key)
        if record is None:
            raise _approval_error("approval_not_found")
        if record.state == "event_failed":
            raise _approval_error("approval_event_failed")
        if record.state == "expired":
            raise _approval_error("approval_expired")
        if record.state in {"cancelled", "interrupted"}:
            raise _approval_error("approval_cancelled")
        if decision != record.decision:
            raise _approval_error("approval_already_resolved")
        outcome = "already_applied" if record.state == "applied" else "already_denied"
        return self._decision_payload(record, outcome)

    def _append_requested(self, record: _ApprovalRecord) -> bool:
        try:
            self._runtime.sessions.append_event(
                session_id=record.session_id,
                event_type=DESKTOP_APPROVAL_REQUESTED,
                payload={
                    "approval_id": record.approval_id,
                    "call_id": record.call_id,
                    "tool_name": record.tool_name,
                    "argument_keys": list(record.argument_keys),
                    "argument_keys_count": len(record.argument_keys),
                    "requested_at": record.requested_at,
                    "expires_at": record.expires_at,
                    "request_id": record.trace_id,
                    "state": "pending",
                },
                trace_id=record.trace_id,
            )
        except Exception:
            return False
        return True

    def _append_resolved(
        self,
        record: _ApprovalRecord,
        *,
        decision: str | None,
        outcome: str,
        resolved_at: str,
    ) -> bool:
        try:
            self._runtime.sessions.append_event(
                session_id=record.session_id,
                event_type=DESKTOP_APPROVAL_RESOLVED,
                payload={
                    "approval_id": record.approval_id,
                    "call_id": record.call_id,
                    "tool_name": record.tool_name,
                    "decision": decision,
                    "outcome": outcome,
                    "resolved_at": resolved_at,
                    "request_id": record.trace_id,
                    "state": "terminal",
                },
                trace_id=record.trace_id,
            )
        except Exception:
            return False
        return True

    @staticmethod
    def _decision_payload(record: _ApprovalRecord, outcome: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "approval_id": record.approval_id,
            "session_id": record.session_id,
            "trace_id": record.trace_id,
            "call_id": record.call_id,
            "decision": record.decision,
            "outcome": outcome,
            "resolved_at": record.resolved_at,
        }

    def _purge_tombstones_locked(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(seconds=_TOMBSTONE_TTL_SECONDS)
        for key, record in tuple(self._tombstones.items()):
            resolved_at = _parse_timestamp(record.resolved_at or "")
            if resolved_at is None or resolved_at < cutoff:
                self._tombstones.pop(key, None)
        while len(self._tombstones) > _TOMBSTONE_LIMIT:
            self._tombstones.popitem(last=False)

    @staticmethod
    def _valid_request(request: DesktopApprovalRequest) -> bool:
        bounded = (
            request.session_id,
            request.trace_id,
            request.tool_name,
            request.call_id,
        )
        if any(
            not value or len(value.encode("utf-8")) > _KEY_BYTES for value in bounded
        ):
            return False
        keys = request.argument_keys
        if (
            len(keys) > _KEY_LIMIT
            or any(not isinstance(key, str) for key in keys)
            or tuple(sorted(set(keys))) != keys
        ):
            return False
        if any(len(key.encode("utf-8")) > _KEY_BYTES for key in keys):
            return False
        return (
            len(
                json.dumps(keys, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            <= _KEY_ARRAY_BYTES
        )


def _approval_error(code: str) -> ClientApprovalError:
    messages = {
        "approval_not_found": "Approval was not found.",
        "approval_already_resolved": "Approval is already resolved.",
        "approval_expired": "Approval has expired.",
        "approval_cancelled": "Approval was cancelled.",
        "approval_event_failed": "Approval audit event could not be recorded.",
    }
    return ClientApprovalError(code, messages[code])


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _is_expired(value: str) -> bool:
    expires_at = _parse_timestamp(value)
    return expires_at is None or expires_at <= datetime.now(UTC)


def install_approvals(
    handler_cls: Any, runtime: Any
) -> ClientApprovalCoordinator | None:
    client_auth = getattr(handler_cls, "client_auth", None)
    coordinator = (
        ClientApprovalCoordinator(client_auth=client_auth, runtime=runtime)
        if client_auth is not None and runtime is not None
        else None
    )
    setattr(handler_cls, "client_approvals", coordinator)
    return coordinator


def close_approvals(coordinator: ClientApprovalCoordinator | None) -> None:
    if coordinator is not None:
        coordinator.close("shutdown")


__all__ = [
    "ClientApprovalCoordinator",
    "ClientApprovalError",
    "close_approvals",
    "install_approvals",
]
