"""Structural A2A and MCP mapping for delegated memory authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from openminion.modules.memory.errors import (
    ConstraintViolationError,
    InvalidArgumentError,
)
from sophiagraph.access import AccessConstraint, MemoryAccessContext

_FORBIDDEN_KEYS = frozenset(
    {"authorization", "access_token", "bearer_token", "query", "policy"}
)


@dataclass(frozen=True, slots=True)
class DelegatedTransportProjection:
    """Trusted access context plus the non-secret grant correlation id."""

    context: MemoryAccessContext
    grant_id: str


def map_delegated_memory_transport(
    payload: Mapping[str, Any],
    *,
    trusted_principal_id: str,
    trusted_audience: str,
    constraints: tuple[AccessConstraint, ...] = (),
) -> DelegatedTransportProjection:
    """Map trusted transport facts without forwarding credentials or free text."""

    forbidden = _FORBIDDEN_KEYS.intersection(payload)
    if forbidden:
        raise InvalidArgumentError(
            f"forbidden delegated transport fields: {sorted(forbidden)}"
        )
    principal_id = str(payload.get("principal_id") or "").strip()
    audience = str(payload.get("audience") or "").strip()
    if principal_id != trusted_principal_id or audience != trusted_audience:
        raise ConstraintViolationError(
            "delegated transport principal or audience mismatch"
        )
    required = {
        key: str(payload.get(key) or "").strip()
        for key in (
            "grant_id",
            "subject_agent_id",
            "parent_run_id",
            "child_run_id",
            "trace_parent_id",
        )
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise InvalidArgumentError(
            f"missing delegated transport fields: {', '.join(missing)}"
        )
    context = MemoryAccessContext(
        principal_id=trusted_principal_id,
        audience=trusted_audience,
        subject_agent_id=required["subject_agent_id"],
        parent_run_id=required["parent_run_id"],
        child_run_id=required["child_run_id"],
        trace_parent_id=required["trace_parent_id"],
        constraints=constraints,
        delegated=True,
        evidence_refs=(
            f"transport:{str(payload.get('transport') or 'unknown').strip()}",
        ),
    )
    return DelegatedTransportProjection(context=context, grant_id=required["grant_id"])


__all__ = ["DelegatedTransportProjection", "map_delegated_memory_transport"]
