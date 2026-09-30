"""OpenMinion projection of authoritative policy grants into Sophiagraph access."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from openminion.modules.memory.adapters.contracts import (
    ActivePolicyGrantResolver,
    DelegatedRunContextView,
)
from sophiagraph.access import (
    AccessConstraint,
    AuthorizedSophiaGraphGateway,
    DelegationMemoryGrant,
    DelegatedMemoryAccessDeniedError,
    MemoryAccessContext,
    MemoryAccessOperation,
    MemoryAccessRequest,
    intersect_memory_namespaces,
)
from sophiagraph.contracts.errors import InvalidArgumentError
from sophiagraph.models import MemoryNamespace
from sophiagraph.query import SearchQueryOptions

from openminion.modules.memory.observability.delegated_access import (
    DelegatedMemoryTelemetryBridge,
)
from openminion.modules.prompting.context_blocks import DELEGATED_MEMORY_BLOCK_HEADER


@dataclass(frozen=True, slots=True)
class DelegatedContextBudgetResult:
    segments: tuple[Any, ...]
    omitted_segment_ids: tuple[str, ...]
    used_tokens: int
    max_context_tokens: int


class DelegatedContextBudgetError(ValueError):
    """Typed failure for malformed delegated context budgets."""

    code = "DELEGATED_MEMORY_CONTEXT_BUDGET_INVALID"


@dataclass(frozen=True, slots=True)
class DelegatedMemoryContextResult:
    text: str
    selected_count: int
    omitted_count: int
    reason: str


@dataclass(frozen=True, slots=True)
class _RenderedRecord:
    id: str
    text: str
    token_estimate: int


@dataclass(frozen=True, slots=True)
class _MetadataRunContext:
    parent_agent_id: str
    child_agent_id: str
    parent_run_id: str
    child_run_id: str
    trace_parent_id: str
    memory_posture: str
    memory_grant_id: str | None
    cancelled: bool = False


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


def build_delegated_memory_context(
    *,
    policy: Any,
    store: Any | None,
    inbound_metadata: Mapping[str, str],
    query: str,
    telemetry_service: Any | None,
    session_id: str,
    turn_id: str,
) -> DelegatedMemoryContextResult:
    """Resolve and render one trusted child read without ambient fallback."""

    run_context = _run_context_from_metadata(inbound_metadata)
    bridge = DelegatedMemoryTelemetryBridge(
        telemetry_service=telemetry_service,
        session_id=session_id,
        turn_id=turn_id,
    )
    if store is None or policy is None:
        return _selection_result(bridge, reason="delegated_memory_unavailable")
    resolved = _resolve_delegated_access(
        policy=policy,
        store=store,
        run_context=run_context,
        bridge=bridge,
    )
    if resolved is None:
        return _selection_result(bridge, reason="grant_unresolved")
    gateway, context, request, namespaces, max_results, max_context_tokens = resolved
    try:
        records = gateway.search_records(
            SearchQueryOptions(
                query=query,
                scopes=_namespace_scopes(namespaces),
                types=list(request.record_types),
                include_invalidated=False,
                limit=max_results,
                namespaces=list(namespaces),
            ),
            context=context,
            request=request,
        )
    except DelegatedMemoryAccessDeniedError as exc:
        return _selection_result(bridge, reason=str(exc.decision.reason))
    rendered = tuple(_render_record(record) for record in records)
    record_budget = max_context_tokens - max(1, len(DELEGATED_MEMORY_BLOCK_HEADER) // 4)
    if rendered and record_budget <= 0:
        return _selection_result(
            bridge,
            omitted_count=len(rendered),
            reason="context_budget_exhausted",
        )
    bounded = enforce_delegated_context_budget(
        rendered,
        max_context_tokens=max(1, record_budget),
    )
    selected = tuple(bounded.segments)
    text = ""
    if selected:
        text = "\n".join(
            [DELEGATED_MEMORY_BLOCK_HEADER, *(item.text for item in selected)]
        )
    return _selection_result(
        bridge,
        text=text,
        selected_count=len(selected),
        omitted_count=len(bounded.omitted_segment_ids),
        reason="selected" if selected else "no_match",
    )


def _resolve_delegated_access(
    *,
    policy: Any,
    store: Any,
    run_context: _MetadataRunContext,
    bridge: DelegatedMemoryTelemetryBridge,
) -> (
    tuple[
        AuthorizedSophiaGraphGateway,
        MemoryAccessContext,
        MemoryAccessRequest,
        tuple[MemoryNamespace, ...],
        int,
        int,
    ]
    | None
):
    grant = next(
        (
            item
            for item in policy.list_grants(
                subject_id=run_context.child_agent_id,
                effect="allow",
                tool="memory",
                method="delegated_read",
                active_only=True,
            )
            if item.grant_id == run_context.memory_grant_id
        ),
        None,
    )
    if grant is None:
        return None
    delegated = grant.target_json.get("delegated_memory")
    if not isinstance(delegated, Mapping):
        return None
    namespaces = tuple(
        MemoryNamespace.from_dict(dict(item))
        for item in delegated.get("namespaces", ())
    )
    workspace_ids = tuple(str(item) for item in delegated.get("workspace_ids", ()))
    record_types = tuple(str(item) for item in delegated.get("record_types", ()))
    max_results = int(delegated.get("max_results", 0))
    max_context_tokens = int(delegated.get("max_context_tokens", 0))
    request = MemoryAccessRequest(
        operation="read",
        grant_id=run_context.memory_grant_id,
        namespaces=namespaces,
        workspace_ids=workspace_ids,
        record_types=record_types,
        max_results=max_results,
        max_context_tokens=max_context_tokens,
    )
    context = MemoryAccessContext(
        principal_id=run_context.child_agent_id,
        audience="sophiagraph",
        subject_agent_id=run_context.child_agent_id,
        parent_run_id=run_context.parent_run_id,
        child_run_id=run_context.child_run_id,
        trace_parent_id=run_context.trace_parent_id,
        constraints=(
            AccessConstraint(
                mode="allowlist",
                namespaces=namespaces,
                workspace_ids=workspace_ids,
                operations=("read",),
                record_types=record_types,
                max_results=max_results,
                max_context_tokens=max_context_tokens,
            ),
        ),
        delegated=True,
        host_max_results=max_results,
        host_max_context_tokens=max_context_tokens,
    )
    resolver = OpenMinionDelegationMemoryGrantResolver(
        policy,
        run_context,
        memory_scope_namespaces=namespaces,
    )
    gateway = AuthorizedSophiaGraphGateway(
        store,
        resolver=resolver,
        telemetry_recorder=bridge,
    )
    return (
        gateway,
        context,
        request,
        namespaces,
        max_results,
        max_context_tokens,
    )


def _run_context_from_metadata(
    inbound_metadata: Mapping[str, str],
) -> _MetadataRunContext:
    return _MetadataRunContext(
        parent_agent_id=inbound_metadata["subagent_parent_agent_id"],
        child_agent_id=inbound_metadata["subagent_child_agent_id"],
        parent_run_id=inbound_metadata["subagent_parent_run_id"],
        child_run_id=inbound_metadata["subagent_child_run_id"],
        trace_parent_id=inbound_metadata["subagent_trace_parent_id"],
        memory_posture=inbound_metadata["subagent_memory_posture"],
        memory_grant_id=inbound_metadata.get("subagent_memory_grant_id") or None,
        cancelled=inbound_metadata.get("subagent_cancelled") == "true",
    )


def _render_record(record: Any) -> _RenderedRecord:
    content = record.content
    if isinstance(content, str):
        body = content.strip()
    else:
        body = json.dumps(content, ensure_ascii=True, sort_keys=True)
    text = f"- [{record.id}] {body}"
    return _RenderedRecord(
        id=str(record.id),
        text=text,
        token_estimate=max(1, len(text) // 4),
    )


def _namespace_scopes(namespaces: tuple[MemoryNamespace, ...]) -> list[str]:
    scopes: list[str] = []
    for namespace in namespaces:
        for kind, value in (
            ("session", namespace.session_id),
            ("agent", namespace.agent_id),
            ("project", namespace.project_id),
            ("global", namespace.graph_id),
        ):
            if value:
                scope = f"{kind}:{value}"
                if scope not in scopes:
                    scopes.append(scope)
    return scopes


def _selection_result(
    bridge: DelegatedMemoryTelemetryBridge,
    *,
    text: str = "",
    selected_count: int = 0,
    omitted_count: int = 0,
    reason: str,
) -> DelegatedMemoryContextResult:
    bridge.record_selection(
        selected_count=selected_count,
        omitted_count=omitted_count,
        reason=reason,
    )
    return DelegatedMemoryContextResult(
        text=text,
        selected_count=selected_count,
        omitted_count=omitted_count,
        reason=reason,
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
    "DelegatedMemoryContextResult",
    "OpenMinionDelegationMemoryGrantResolver",
    "build_delegated_memory_context",
    "enforce_delegated_context_budget",
    "authorize_and_enforce_delegated_context",
]
