from collections.abc import Mapping
from dataclasses import fields
from datetime import datetime, timezone
from typing import Callable, Literal
import uuid

from pydantic import BaseModel, Field
from sophiagraph.contracts.errors import InvalidArgumentError as SophiaInvalidArgumentError

from .constants import (
    MEMORY_SCOPE_BOUNDARY_EVENT_TYPE as _SCOPE_BOUNDARY_EVENT_TYPE,
    MEMORY_SCOPE_BOUNDARY_LEDGER_MAX_EVENTS as _LEDGER_MAX,
)
from ..errors import InvalidArgumentError
from ..models import MemoryNamespace, MemoryScope


ScopeAccessMode = Literal[
    "agent_only",
    "agent_plus_global",
    "session_plus_agent",
    "project_plus_agent",
]


ScopeOperation = Literal["read", "write"]


_GLOBAL_DEFAULT_SCOPE = "global:default"


def resolve_namespace_filter(
    *,
    scope: str | None = None,
    namespace: Mapping[str, object] | MemoryNamespace | None = None,
) -> MemoryNamespace:
    """Resolve one typed namespace filter from explicit product inputs."""

    allowed_fields = {field.name for field in fields(MemoryNamespace)}
    if isinstance(namespace, MemoryNamespace):
        values = namespace.as_dict()
    elif namespace is None:
        values = {}
    elif isinstance(namespace, Mapping):
        if not namespace:
            raise InvalidArgumentError("namespace must not be empty")
        unknown = sorted(set(namespace) - allowed_fields)
        if unknown:
            raise InvalidArgumentError(
                f"unknown namespace fields: {', '.join(unknown)}"
            )
        invalid_types = sorted(
            key
            for key, value in namespace.items()
            if value is not None and not isinstance(value, str)
        )
        if invalid_types:
            raise InvalidArgumentError(
                f"namespace fields must be strings: {', '.join(invalid_types)}"
            )
        values = {
            key: value.strip()
            for key, value in namespace.items()
            if isinstance(value, str) and value.strip()
        }
        if not values:
            raise InvalidArgumentError("namespace must contain at least one id")
    else:
        raise InvalidArgumentError("namespace must be an object")

    normalized_scope = str(scope or "").strip()
    if normalized_scope:
        try:
            parsed = MemoryScope.parse(normalized_scope)
        except SophiaInvalidArgumentError as exc:
            raise InvalidArgumentError(str(exc)) from exc
        scope_field = {
            "session": "session_id",
            "agent": "agent_id",
            "project": "project_id",
            "global": "graph_id",
        }[parsed.kind]
        existing = values.get(scope_field)
        if existing is not None and existing != parsed.value:
            raise InvalidArgumentError(
                f"conflicting namespace {scope_field}: "
                f"{existing!r} != {parsed.value!r}"
            )
        values[scope_field] = parsed.value

    if not values:
        raise InvalidArgumentError("scope or namespace is required")
    try:
        return MemoryNamespace.from_dict(values)
    except SophiaInvalidArgumentError as exc:
        raise InvalidArgumentError(str(exc)) from exc


class ScopeAccessDecision(BaseModel):
    """Typed scope-access decision for one read seam."""

    agent_id: str
    mode: ScopeAccessMode
    scopes: list[str]
    caller_seam: str
    decided_at: str = Field(default="")


class ScopeBoundaryEvent(BaseModel):
    """Audit event for each widened read or write."""

    event_id: str
    agent_id: str
    mode: ScopeAccessMode
    scopes: list[str]
    operation: ScopeOperation
    caller_seam: str
    recorded_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_agent_id(agent_id: str) -> str:
    normalized = str(agent_id or "").strip()
    if not normalized:
        raise InvalidArgumentError("agent_id must be a non-empty constant string")
    return normalized


def _require_seam(caller_seam: str) -> str:
    if not isinstance(caller_seam, str) or not caller_seam.strip():
        raise InvalidArgumentError("caller_seam must be a non-empty constant string")
    return caller_seam


def build_agent_read_scopes(
    agent_id: str,
    *,
    mode: ScopeAccessMode,
    caller_seam: str,
    session_id: str | None = None,
    project_id: str | None = None,
) -> ScopeAccessDecision:
    """Construct the typed scope list for one read seam."""

    normalized_agent = _require_agent_id(agent_id)
    seam = _require_seam(caller_seam)
    agent_scope = build_agent_write_scope(normalized_agent)
    if mode == "agent_only":
        scopes = [agent_scope]
    elif mode == "agent_plus_global":
        scopes = [agent_scope, _GLOBAL_DEFAULT_SCOPE]
    elif mode == "session_plus_agent":
        if not (session_id and str(session_id).strip()):
            raise InvalidArgumentError(
                "session_plus_agent mode requires a non-empty session_id"
            )
        scopes = [f"session:{str(session_id).strip()}", agent_scope]
    elif mode == "project_plus_agent":
        if not (project_id and str(project_id).strip()):
            raise InvalidArgumentError(
                "project_plus_agent mode requires a non-empty project_id"
            )
        scopes = [f"project:{str(project_id).strip()}", agent_scope]
    else:
        raise InvalidArgumentError(f"unknown ScopeAccessMode: {mode!r}")

    return ScopeAccessDecision(
        agent_id=normalized_agent,
        mode=mode,
        scopes=list(dict.fromkeys(scopes)),
        caller_seam=seam,
        decided_at=_now_iso(),
    )


def build_agent_write_scope(agent_id: str) -> str:
    """Return the canonical `agent:<agent_id>` write scope."""

    return f"agent:{_require_agent_id(agent_id)}"


def assert_scope_matches_agent(scope: str, agent_id: str) -> None:
    """Structural guard at the service boundary; raises on cross-agent leak."""

    normalized_agent = _require_agent_id(agent_id)
    normalized_scope = str(scope or "").strip()
    if not normalized_scope:
        raise InvalidArgumentError("scope must be a non-empty string")
    if not normalized_scope.startswith("agent:"):
        return
    expected = build_agent_write_scope(normalized_agent)
    if normalized_scope != expected:
        raise PermissionError(
            f"cross-agent scope leak: scope {normalized_scope!r} "
            f"does not match agent_id {normalized_agent!r}"
        )


def record_scope_boundary_event(
    decision: ScopeAccessDecision,
    *,
    operation: ScopeOperation,
    audit_log: Callable[[ScopeBoundaryEvent], None] | None = None,
) -> ScopeBoundaryEvent:
    """Emit a `ScopeBoundaryEvent` for one scope-access decision."""

    if operation not in ("read", "write"):
        raise InvalidArgumentError(f"unknown ScopeOperation: {operation!r}")
    event = ScopeBoundaryEvent(
        event_id=uuid.uuid4().hex,
        agent_id=decision.agent_id,
        mode=decision.mode,
        scopes=list(decision.scopes),
        operation=operation,
        caller_seam=decision.caller_seam,
        recorded_at=_now_iso(),
    )
    if audit_log is not None:
        audit_log(event)
    return event


_event_ledger: list[ScopeBoundaryEvent] = []


def append_to_ledger(event: ScopeBoundaryEvent) -> None:
    """Append an event to the bounded process-local ledger."""

    _event_ledger.append(event)
    overflow = len(_event_ledger) - _LEDGER_MAX
    if overflow > 0:
        del _event_ledger[:overflow]


def drain_ledger() -> list[ScopeBoundaryEvent]:
    """Return and clear the ledger. Tests use this for parity assertions."""

    snapshot = list(_event_ledger)
    _event_ledger.clear()
    return snapshot


def snapshot_ledger() -> list[ScopeBoundaryEvent]:
    """Return a copy of the ledger without clearing."""

    return list(_event_ledger)


def emit_read_decision(
    agent_id: str,
    *,
    mode: ScopeAccessMode,
    caller_seam: str,
    session_id: str | None = None,
    project_id: str | None = None,
) -> tuple[list[str], ScopeBoundaryEvent | None]:
    """Build a read decision and emit an audit event when widened."""

    decision = build_agent_read_scopes(
        agent_id,
        mode=mode,
        caller_seam=caller_seam,
        session_id=session_id,
        project_id=project_id,
    )
    if mode == "agent_only":
        return decision.scopes, None
    event = record_scope_boundary_event(
        decision,
        operation="read",
        audit_log=append_to_ledger,
    )
    return decision.scopes, event


def emit_write_decision(
    agent_id: str,
    *,
    caller_seam: str,
) -> tuple[str, ScopeBoundaryEvent]:
    """Build the canonical write scope and emit the audit event."""

    decision = build_agent_read_scopes(
        agent_id,
        mode="agent_only",
        caller_seam=caller_seam,
    )
    scope = build_agent_write_scope(agent_id)
    event = record_scope_boundary_event(
        decision,
        operation="write",
        audit_log=append_to_ledger,
    )
    return scope, event


SCOPE_BOUNDARY_EVENT_TYPE = _SCOPE_BOUNDARY_EVENT_TYPE


__all__ = [
    "SCOPE_BOUNDARY_EVENT_TYPE",
    "ScopeAccessDecision",
    "ScopeAccessMode",
    "ScopeBoundaryEvent",
    "ScopeOperation",
    "append_to_ledger",
    "assert_scope_matches_agent",
    "build_agent_read_scopes",
    "build_agent_write_scope",
    "drain_ledger",
    "emit_read_decision",
    "emit_write_decision",
    "record_scope_boundary_event",
    "resolve_namespace_filter",
    "snapshot_ledger",
]
