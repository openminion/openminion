"""OpenMinion projection of authoritative policy grants into Sophiagraph access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from openminion.modules.memory.adapters.contracts import (
    ActivePolicyGrantResolver,
    DelegatedRunContextView,
)
from sophiagraph.access import (
    DelegationMemoryGrant,
    MemoryAccessContext,
    MemoryAccessOperation,
    intersect_memory_namespaces,
)
from sophiagraph.contracts.errors import InvalidArgumentError
from sophiagraph.models import MemoryNamespace


@dataclass(frozen=True, slots=True)
class DelegatedContextBudgetResult:
    segments: tuple[Any, ...]
    omitted_segment_ids: tuple[str, ...]
    used_tokens: int
    max_context_tokens: int


class DelegatedContextBudgetError(ValueError):
    """Typed failure for malformed delegated context budgets."""

    code = "DELEGATED_MEMORY_CONTEXT_BUDGET_INVALID"


class OpenMinionDelegationMemoryGrantResolver:
    """Resolve each operation from OpenMinion's authoritative policy owner."""

    def __init__(
        self,
        policy: ActivePolicyGrantResolver,
        run_context: DelegatedRunContextView,
        *,
        memory_scope_namespaces: tuple[MemoryNamespace, ...] = (),
    ) -> None:
        self._policy = policy
        self._run_context = run_context
        self._memory_scope_namespaces = memory_scope_namespaces

    def resolve_grant(
        self,
        grant_id: str,
        *,
        context: MemoryAccessContext,
        operation: MemoryAccessOperation,
    ) -> DelegationMemoryGrant | None:
        if (
            self._run_context.memory_posture == "none"
            or grant_id != self._run_context.memory_grant_id
            or operation != "read"
            or self._run_context.cancelled
        ):
            return None
        grant = self._policy.resolve_active_grant_for_use(
            grant_id,
            subject_id=self._run_context.child_agent_id,
            tool="memory",
            method="delegated_read",
            required_target={
                "resource": "sophiagraph",
                "delegated_memory": {
                    "version": 1,
                    "audience": "sophiagraph",
                    "delegator_agent_id": self._run_context.parent_agent_id,
                    "subject_agent_id": self._run_context.child_agent_id,
                    "parent_run_id": self._run_context.parent_run_id,
                    "child_run_id": self._run_context.child_run_id,
                    "trace_parent_id": self._run_context.trace_parent_id,
                },
            },
        )
        if grant is None:
            return None
        return _project_grant(
            grant,
            self._run_context,
            operation=operation,
            memory_scope_namespaces=self._memory_scope_namespaces,
        )


def enforce_delegated_context_budget(
    segments: list[Any] | tuple[Any, ...],
    *,
    max_context_tokens: int,
) -> DelegatedContextBudgetResult:
    """Bound already-structured context segments before model delivery."""

    if not isinstance(max_context_tokens, int) or max_context_tokens <= 0:
        raise DelegatedContextBudgetError("max_context_tokens must be positive")
    selected: list[Any] = []
    omitted: list[str] = []
    used = 0
    for segment in segments:
        cost = int(getattr(segment, "token_estimate", 0))
        if cost < 0:
            raise DelegatedContextBudgetError(
                "segment token_estimate must be non-negative"
            )
        if used + cost > max_context_tokens:
            omitted.append(str(getattr(segment, "id", "unknown")))
            continue
        selected.append(segment)
        used += cost
    return DelegatedContextBudgetResult(
        segments=tuple(selected),
        omitted_segment_ids=tuple(omitted),
        used_tokens=used,
        max_context_tokens=max_context_tokens,
    )


def authorize_and_enforce_delegated_context(
    gateway: Any,
    segments: list[Any] | tuple[Any, ...],
    *,
    context: Any,
    request: Any,
) -> DelegatedContextBudgetResult:
    """Refresh authorization, then enforce its effective model-context budget."""

    decision = gateway.require(context, request)
    return enforce_delegated_context_budget(
        segments,
        max_context_tokens=decision.max_context_tokens,
    )


def _project_grant(
    grant: Any,
    run_context: DelegatedRunContextView,
    *,
    operation: str,
    memory_scope_namespaces: tuple[MemoryNamespace, ...],
) -> DelegationMemoryGrant | None:
    target = grant.target_json
    if not isinstance(target, Mapping):
        return None
    delegated = target.get("delegated_memory")
    if target.get("resource") != "sophiagraph" or not isinstance(delegated, Mapping):
        return None
    expected = {
        "version": 1,
        "audience": "sophiagraph",
        "delegator_agent_id": run_context.parent_agent_id,
        "subject_agent_id": run_context.child_agent_id,
        "parent_run_id": run_context.parent_run_id,
        "child_run_id": run_context.child_run_id,
        "trace_parent_id": run_context.trace_parent_id,
    }
    if any(delegated.get(key) != value for key, value in expected.items()):
        return None
    if grant.subject_id != run_context.child_agent_id or grant.expires_at is None:
        return None
    try:
        namespaces = tuple(
            MemoryNamespace.from_dict(dict(value))
            for value in delegated.get("namespaces", ())
        )
    except (TypeError, ValueError, InvalidArgumentError):
        return None
    if memory_scope_namespaces:
        namespaces = intersect_memory_namespaces(
            namespaces,
            memory_scope_namespaces,
        )
    operations = tuple(str(value) for value in delegated.get("operations", ()))
    if operation not in operations:
        return None
    try:
        return DelegationMemoryGrant(
            grant_id=grant.grant_id,
            issuer_authority="openminion-policy",
            audience="sophiagraph",
            delegator_agent_id=run_context.parent_agent_id,
            subject_agent_id=run_context.child_agent_id,
            parent_run_id=run_context.parent_run_id,
            child_run_id=run_context.child_run_id,
            trace_parent_id=run_context.trace_parent_id,
            namespaces=namespaces,
            workspace_ids=tuple(
                str(value) for value in delegated.get("workspace_ids", ())
            ),
            operations=operations,
            record_types=tuple(
                str(value) for value in delegated.get("record_types", ())
            ),
            issued_at=grant.created_at,
            expires_at=grant.expires_at,
            max_results=int(delegated.get("max_results", 0)),
            max_context_tokens=int(delegated.get("max_context_tokens", 0)),
            parent_grant_id=delegated.get("parent_grant_id"),
            current_depth=int(delegated.get("current_depth", 1)),
            max_depth=int(delegated.get("max_depth", 1)),
            can_reshare=bool(delegated.get("can_reshare", False)),
        )
    except (TypeError, ValueError, InvalidArgumentError):
        return None


__all__ = [
    "DelegatedContextBudgetResult",
    "DelegatedContextBudgetError",
    "OpenMinionDelegationMemoryGrantResolver",
    "enforce_delegated_context_budget",
    "authorize_and_enforce_delegated_context",
]
