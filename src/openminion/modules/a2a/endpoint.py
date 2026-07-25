from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from openminion.modules.a2a import A2ARuntime
from openminion.modules.a2a.models import Envelope
from openminion.modules.a2a.storage import (
    MemoryAuditStore,
    MemoryStateStore,
    SQLiteStateStore,
)

EXTERNAL_A2A_RUNTIME_ATTR = "_external_a2a_runtime"
_EXTERNAL_A2A_RUNTIME_LOCK = threading.RLock()


def resolve_external_a2a_runtime(owner: object) -> A2ARuntime:
    with _EXTERNAL_A2A_RUNTIME_LOCK:
        cached = getattr(owner, EXTERNAL_A2A_RUNTIME_ATTR, None)
        if isinstance(cached, A2ARuntime):
            return cached
        runtime = build_external_a2a_runtime(owner)
        setattr(owner, EXTERNAL_A2A_RUNTIME_ATTR, runtime)
        return runtime


def close_external_a2a_runtime(owner: object) -> None:
    with _EXTERNAL_A2A_RUNTIME_LOCK:
        runtime = getattr(owner, EXTERNAL_A2A_RUNTIME_ATTR, None)
        if not isinstance(runtime, A2ARuntime):
            return
        try:
            delattr(owner, EXTERNAL_A2A_RUNTIME_ATTR)
        except AttributeError:
            pass
    runtime.close(wait=False)


def build_external_a2a_runtime(owner: object) -> A2ARuntime:
    storage_path = getattr(owner, "storage_path", None)
    state_store = (
        SQLiteStateStore(_state_db_path(Path(storage_path)))
        if storage_path is not None
        else MemoryStateStore()
    )
    runtime = A2ARuntime(state_store=state_store, audit_store=MemoryAuditStore())
    runtime.register_agent(
        "openminion.local",
        ["tasks/", "echo.", "message."],
        lambda envelope: _default_external_agent_handler(owner, envelope),
        tags=["openminion", "external-a2a"],
    )
    return runtime


def _default_external_agent_handler(
    owner: object, envelope: Envelope
) -> dict[str, Any]:
    run_turn = getattr(owner, "run_turn", None)
    if not callable(run_turn):
        raise RuntimeError("External A2A endpoint requires APIRuntime.run_turn")
    metadata = _metadata(envelope)
    message = _message_text(envelope.params.get("message"))
    if not message:
        raise ValueError("External A2A tasks/send requires a non-empty message")
    payload = {
        "session_id": str(metadata.get("session_id") or f"a2a:{envelope.trace_id}"),
        "message": message,
        "channel": "a2a",
        "target": envelope.from_agent,
        "trace_id": envelope.trace_id,
        "idempotency_key": envelope.idempotency_key,
        "inbound_metadata": _inbound_metadata(envelope=envelope, metadata=metadata),
    }
    agent_id = str(metadata.get("agent_id") or "").strip()
    if agent_id:
        payload["agent_id"] = agent_id
    return {
        "agent": "openminion.local",
        "method": envelope.method,
        "trace_id": envelope.trace_id,
        "turn": run_turn(payload=payload, request_id=envelope.trace_id),
    }


def _metadata(envelope: Envelope) -> dict[str, Any]:
    raw = envelope.params.get("metadata", {})
    return dict(raw) if isinstance(raw, dict) else {}


def _message_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        direct = str(raw.get("text") or "").strip()
        if direct:
            return direct
        parts = raw.get("parts")
        if isinstance(parts, list):
            text_parts = [
                str(part.get("text") or "").strip()
                for part in parts
                if isinstance(part, dict) and str(part.get("text") or "").strip()
            ]
            if text_parts:
                return "\n".join(text_parts)
    return ""


def _inbound_metadata(
    *, envelope: Envelope, metadata: dict[str, Any]
) -> dict[str, str]:
    inbound = {
        "a2a_external": "true",
        "a2a_from_agent": envelope.from_agent,
        "a2a_method": envelope.method,
        "a2a_msg_id": envelope.msg_id,
        "a2a_trace_id": envelope.trace_id,
    }
    for key in ("workspace_root", "cwd", "source_client"):
        value = str(metadata.get(key) or "").strip()
        if value:
            inbound[key] = value
    return inbound


def _state_db_path(storage_path: Path) -> str:
    return str(
        storage_path.expanduser().resolve(strict=False).parent / "a2a-network-state.db"
    )


__all__ = [
    "EXTERNAL_A2A_RUNTIME_ATTR",
    "build_external_a2a_runtime",
    "close_external_a2a_runtime",
    "resolve_external_a2a_runtime",
]
