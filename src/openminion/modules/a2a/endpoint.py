from __future__ import annotations

from pathlib import Path
from typing import Any

from openminion.modules.a2a import A2ARuntime
from openminion.modules.a2a.models import Envelope
from openminion.modules.a2a.storage import MemoryAuditStore, MemoryStateStore, SQLiteStateStore

EXTERNAL_A2A_RUNTIME_ATTR = "_external_a2a_runtime"


def resolve_external_a2a_runtime(owner: object) -> A2ARuntime:
    cached = getattr(owner, EXTERNAL_A2A_RUNTIME_ATTR, None)
    if isinstance(cached, A2ARuntime):
        return cached
    runtime = build_external_a2a_runtime(owner)
    setattr(owner, EXTERNAL_A2A_RUNTIME_ATTR, runtime)
    return runtime


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
        _default_external_agent_handler,
        tags=["openminion", "external-a2a"],
    )
    return runtime


def _default_external_agent_handler(envelope: Envelope) -> dict[str, Any]:
    return {
        "agent": "openminion.local",
        "method": envelope.method,
        "trace_id": envelope.trace_id,
        "message": envelope.params.get("message"),
        "metadata": envelope.params.get("metadata", {}),
    }


def _state_db_path(storage_path: Path) -> str:
    return str(
        storage_path.expanduser().resolve(strict=False).parent / "a2a-network-state.db"
    )


__all__ = [
    "EXTERNAL_A2A_RUNTIME_ATTR",
    "build_external_a2a_runtime",
    "resolve_external_a2a_runtime",
]
