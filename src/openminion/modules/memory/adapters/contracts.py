"""Structural context contract consumed by delegated-memory adapters."""

from __future__ import annotations

from typing import Any, Protocol


class ActivePolicyGrantResolver(Protocol):
    """Policy capability needed to resolve one active delegated grant."""

    def resolve_active_grant_for_use(
        self,
        grant_id: str,
        **criteria: Any,
    ) -> Any | None: ...


class DelegatedRunContextView(Protocol):
    """Minimal run-context fields needed at the memory boundary."""

    parent_agent_id: str
    child_agent_id: str
    parent_run_id: str
    child_run_id: str
    trace_parent_id: str
    memory_posture: str
    memory_grant_id: str | None
    cancelled: bool


__all__ = ["ActivePolicyGrantResolver", "DelegatedRunContextView"]
