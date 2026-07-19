from __future__ import annotations

from openminion.base.time import utc_now_iso as iso_now
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from .constants import (
    A2A_AGENT_STATUS_ONLINE,
    A2A_IDEMPOTENCY_TERMINAL_STATUSES,
    A2A_TERMINAL_JOB_STATES,
)


def new_uuid() -> str:
    return str(uuid.uuid4())


TERMINAL_JOB_STATES = set(A2A_TERMINAL_JOB_STATES)
IDEMPOTENCY_TERMINAL_STATUSES = set(A2A_IDEMPOTENCY_TERMINAL_STATUSES)

MESSAGE_TYPE_CALL = "call"
MESSAGE_TYPE_RESULT = "result"
MESSAGE_TYPE_JOB_START = "job.start"
MESSAGE_TYPE_JOB_STATUS = "job.status"
MESSAGE_TYPE_JOB_CANCEL = "job.cancel"
MESSAGE_TYPE_EVENT_PUBLISH = "event.publish"

MESSAGE_TYPES = {
    MESSAGE_TYPE_CALL,
    MESSAGE_TYPE_RESULT,
    MESSAGE_TYPE_JOB_START,
    MESSAGE_TYPE_JOB_STATUS,
    MESSAGE_TYPE_JOB_CANCEL,
    MESSAGE_TYPE_EVENT_PUBLISH,
}

IDEMPOTENCY_REQUIRED_TYPES = {MESSAGE_TYPE_CALL, MESSAGE_TYPE_JOB_START}


class EnvelopeValidationError(ValueError):
    """Raised when an envelope violates the contract requirements."""


def validate_envelope_contract(envelope: "Envelope") -> None:
    if not envelope.method.strip():
        raise EnvelopeValidationError("Envelope method is required")
    if not envelope.from_agent.strip():
        raise EnvelopeValidationError("Envelope from_agent is required")
    if not envelope.to_agent and not envelope.to_capability:
        raise EnvelopeValidationError("Either to_agent or to_capability is required")
    if envelope.type not in MESSAGE_TYPES:
        raise EnvelopeValidationError(f"Envelope type '{envelope.type}' is invalid")
    if envelope.requires_idempotency() and not envelope.idempotency_key.strip():
        raise EnvelopeValidationError(
            "idempotency_key is required for call and job.start"
        )


@dataclass
class ArtifactRef:
    ref: str
    mime: str
    sha256: str
    size_bytes: int
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "ref": self.ref,
            "mime": self.mime,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }
        if self.label is not None:
            out["label"] = self.label
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ArtifactRef":
        return cls(
            ref=str(raw.get("ref", "")),
            mime=str(raw.get("mime", "application/octet-stream")),
            sha256=str(raw.get("sha256", "")),
            size_bytes=int(raw.get("size_bytes", 0)),
            label=(None if raw.get("label") is None else str(raw.get("label"))),
        )


@dataclass
class Envelope:
    msg_id: str
    trace_id: str
    ts: str
    from_agent: str
    to_agent: str | None
    to_capability: str | None
    type: str
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    attachments: list[ArtifactRef] = field(default_factory=list)
    timeout_ms: int = 30_000
    idempotency_key: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        from_agent: str,
        to_agent: str | None,
        to_capability: str | None,
        type: str,
        method: str,
        params: dict[str, Any] | None = None,
        timeout_ms: int = 30_000,
        idempotency_key: str = "",
        trace_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> "Envelope":
        return cls(
            msg_id=new_uuid(),
            trace_id=trace_id or new_uuid(),
            ts=iso_now(),
            from_agent=from_agent,
            to_agent=to_agent,
            to_capability=to_capability,
            type=type,
            method=method,
            params=params or {},
            attachments=[],
            timeout_ms=timeout_ms,
            idempotency_key=idempotency_key,
            meta=meta or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "trace_id": self.trace_id,
            "ts": self.ts,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "to_capability": self.to_capability,
            "type": self.type,
            "method": self.method,
            "params": self.params,
            "attachments": [item.to_dict() for item in self.attachments],
            "timeout_ms": self.timeout_ms,
            "idempotency_key": self.idempotency_key,
            "meta": self.meta,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=True)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Envelope":
        return cls(
            msg_id=str(raw.get("msg_id", new_uuid())),
            trace_id=str(raw.get("trace_id", new_uuid())),
            ts=str(raw.get("ts", iso_now())),
            from_agent=str(raw.get("from_agent", "")),
            to_agent=(
                None if raw.get("to_agent") is None else str(raw.get("to_agent"))
            ),
            to_capability=(
                None
                if raw.get("to_capability") is None
                else str(raw.get("to_capability"))
            ),
            type=str(raw.get("type", "")),
            method=str(raw.get("method", "")),
            params=dict(raw.get("params", {})),
            attachments=_artifact_refs_from_raw(raw.get("attachments", [])),
            timeout_ms=int(raw.get("timeout_ms", 30_000)),
            idempotency_key=str(raw.get("idempotency_key", "")),
            meta=dict(raw.get("meta", {})),
        )

    def requires_idempotency(self) -> bool:
        return self.type in IDEMPOTENCY_REQUIRED_TYPES


@dataclass
class IdempotencyRecord:
    key: str
    scope: str
    status: str
    result_inline: dict[str, Any] | None = None
    result_ref: str | None = None
    error: dict[str, Any] | None = None
    task_id: str | None = None
    created_at: str = field(default_factory=iso_now)
    updated_at: str = field(default_factory=iso_now)

    def is_terminal(self) -> bool:
        return self.status in IDEMPOTENCY_TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "scope": self.scope,
            "status": self.status,
            "result_inline": self.result_inline,
            "result_ref": self.result_ref,
            "error": self.error,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class JobRecord:
    task_id: str
    trace_id: str
    idempotency_key: str
    agent_id: str
    method: str
    state: str
    current_step: str = ""
    progress: float = 0.0
    result_inline: dict[str, Any] | None = None
    result_ref: str | None = None
    error: dict[str, Any] | None = None
    created_at: str = field(default_factory=iso_now)
    updated_at: str = field(default_factory=iso_now)
    heartbeat_at: str = field(default_factory=iso_now)

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_JOB_STATES

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "idempotency_key": self.idempotency_key,
            "agent_id": self.agent_id,
            "method": self.method,
            "state": self.state,
            "current_step": self.current_step,
            "progress": self.progress,
            "result_inline": self.result_inline,
            "result_ref": self.result_ref,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "heartbeat_at": self.heartbeat_at,
        }


@dataclass
class AgentDescriptor:
    agent_id: str
    capabilities: list[str]
    endpoint: str
    tags: list[str] = field(default_factory=list)
    status: str = A2A_AGENT_STATUS_ONLINE

    def supports_method(self, method: str) -> bool:
        return any(method.startswith(prefix) for prefix in self.capabilities)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "capabilities": list(self.capabilities),
            "endpoint": self.endpoint,
            "tags": list(self.tags),
            "status": self.status,
        }


@dataclass
class AuditRecord:
    ts: str
    msg_id: str
    trace_id: str
    from_agent: str
    to_agent: str | None
    to_capability: str | None
    type: str
    method: str
    status: str
    task_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    envelope: dict[str, Any] | None = None
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "msg_id": self.msg_id,
            "trace_id": self.trace_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "to_capability": self.to_capability,
            "type": self.type,
            "method": self.method,
            "status": self.status,
            "task_id": self.task_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "envelope": self.envelope,
            "data": self.data,
        }


def _artifact_refs_from_raw(raw_items: Any) -> list[ArtifactRef]:
    if not isinstance(raw_items, list):
        return []
    return [ArtifactRef.from_dict(item) for item in raw_items if isinstance(item, dict)]
