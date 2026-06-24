from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol

from openminion.modules.runtime.constants import INTERVENTION_RECORDED_EVENT_TYPE

InterventionAction = Literal[
    "pause",
    "resume",
    "cancel",
    "kill",
    "redirect",
]

PropagationStatus = Literal[
    "pending",
    "dispatched",
    "no_op",
    "failed",
]

INTERVENTION_ACTIONS: tuple[InterventionAction, ...] = (
    "pause",
    "resume",
    "cancel",
    "kill",
    "redirect",
)

PROPAGATION_STATUSES: tuple[PropagationStatus, ...] = (
    "pending",
    "dispatched",
    "no_op",
    "failed",
)


@dataclass(frozen=True)
class BudgetSnapshot:
    """Typed budget snapshot for live-state projection.

    Operator-facing budget read; values are taken verbatim from the
    budget owner. The seam does not derive budget meaning.
    """

    llm_calls_remaining: int | None = None
    input_tokens_remaining: int | None = None
    output_tokens_remaining: int | None = None
    wall_clock_seconds_remaining: float | None = None


@dataclass(frozen=True)
class LiveAgentState:
    """Typed live projection of an in-flight agent turn.

    Field set matches OBSI audit §5 verbatim. Projection is read-only;
    callers must not mutate this object after construction.
    """

    trace_id: str
    agent_id: str
    phase: str
    lifecycle_phase: str
    pending_tool_calls: tuple[str, ...]
    pending_approvals: tuple[str, ...]
    budget_remaining: BudgetSnapshot
    last_event_type: str
    intervention_capabilities: tuple[InterventionAction, ...]


@dataclass(frozen=True)
class InterventionDecision:
    """Typed operator intervention decision.

    Constructed by `issue_intervention`. Propagation and audit-event id
    are populated by downstream steps via `dataclasses.replace`.
    """

    action: InterventionAction
    operator_id: str
    target_ref: str
    reason: str
    issued_at: datetime
    propagation_status: PropagationStatus = "pending"
    audit_event_id: str = ""


class RuntimeAdapter(Protocol):
    """Adapter for `cancel` and `kill` actions.

    Implementations should wrap `services/runtime/manager.py:cancel_turn`
    and `services/runtime/manager.py:kill_switch`.
    """

    def cancel_turn(self, trace_id: str) -> bool: ...

    def kill_switch(self, grace_s: float = 2.0) -> None: ...


class PauseResumeAdapter(Protocol):
    """Adapter for `pause` and `resume` actions."""

    def pause(self, trace_id: str, *, reason: str) -> bool: ...

    def resume(self, trace_id: str, *, reason: str) -> bool: ...


class RedirectAdapter(Protocol):
    """Adapter for `redirect` actions."""

    def cancel_then_reissue(self, trace_id: str, *, reason: str) -> bool: ...


class AuditLog(Protocol):
    """Audit log adapter for `record_intervention_event`.

    Implementations should wrap the canonical-events stream owner at
    `modules/brain/diagnostics/events.py:CanonicalEventLogger`.
    """

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        trace_id: str | None = None,
    ) -> str: ...


class PhaseStatusSource(Protocol):
    """Typed phase-status source for live-state projection."""

    def current_phase_status(self, trace_id: str) -> Mapping[str, Any] | None: ...


class CanonicalEventSource(Protocol):
    """Canonical-event source for live-state projection."""

    def latest_event_type(self, trace_id: str) -> str | None: ...

    def pending_tool_calls(self, trace_id: str) -> tuple[str, ...]: ...


class ApprovalSource(Protocol):
    """Typed approval/grant source for live-state projection."""

    def pending_approvals(self, trace_id: str) -> tuple[str, ...]: ...


class BudgetSource(Protocol):
    """Typed budget source for live-state projection."""

    def budget_snapshot(self, trace_id: str) -> BudgetSnapshot: ...


_ADAPTER_SELECTION: Mapping[InterventionAction, str] = MappingProxyType(
    {
        "pause": "pause_resume_adapter",
        "resume": "pause_resume_adapter",
        "cancel": "runtime_adapter",
        "kill": "runtime_adapter",
        "redirect": "redirect_adapter",
    }
)


def adapter_slot_for(action: InterventionAction) -> str:
    """Return the adapter slot name used to propagate `action`."""

    return _ADAPTER_SELECTION[action]


def intervention_capabilities_for(
    *,
    lifecycle_phase: str,
    has_active_trace: bool,
) -> tuple[InterventionAction, ...]:
    """Return the typed action set valid against the given lifecycle.

    Pure structural mapping — never inspects prose, transcripts, or
    model output.
    """

    if not has_active_trace:
        return ()
    if lifecycle_phase == "paused":
        return ("resume", "cancel", "kill")
    if lifecycle_phase in ("terminal", "completed", "error"):
        return ()
    return ("pause", "cancel", "kill", "redirect")


def project_live_agent_state(
    trace_id: str,
    *,
    agent_id: str,
    lifecycle_phase: str,
    status_source: PhaseStatusSource,
    event_source: CanonicalEventSource,
    approval_source: ApprovalSource,
    budget_source: BudgetSource,
) -> LiveAgentState:
    """Project the typed live agent state."""

    phase_status = status_source.current_phase_status(trace_id) or {}
    phase = str(phase_status.get("status_key") or "")
    last_event_type = event_source.latest_event_type(trace_id) or ""
    pending_tool_calls = tuple(event_source.pending_tool_calls(trace_id))
    pending_approvals = tuple(approval_source.pending_approvals(trace_id))
    budget_remaining = budget_source.budget_snapshot(trace_id)
    has_active_trace = bool(phase) or last_event_type != "" or bool(pending_tool_calls)
    capabilities = intervention_capabilities_for(
        lifecycle_phase=lifecycle_phase,
        has_active_trace=has_active_trace,
    )
    return LiveAgentState(
        trace_id=trace_id,
        agent_id=agent_id,
        phase=phase,
        lifecycle_phase=lifecycle_phase,
        pending_tool_calls=pending_tool_calls,
        pending_approvals=pending_approvals,
        budget_remaining=budget_remaining,
        last_event_type=last_event_type,
        intervention_capabilities=capabilities,
    )


def issue_intervention(
    action: InterventionAction,
    *,
    operator_id: str,
    target_ref: str,
    reason: str,
    issued_at: datetime | None = None,
) -> InterventionDecision:
    """Construct an `InterventionDecision`."""

    if action not in INTERVENTION_ACTIONS:
        raise ValueError(
            f"unknown intervention action: {action!r}; "
            f"must be one of {INTERVENTION_ACTIONS}"
        )
    return InterventionDecision(
        action=action,
        operator_id=operator_id,
        target_ref=target_ref,
        reason=reason,
        issued_at=issued_at or datetime.now(timezone.utc),
    )


def propagate_intervention(
    decision: InterventionDecision,
    *,
    runtime_adapter: RuntimeAdapter,
    pause_resume_adapter: PauseResumeAdapter,
    redirect_adapter: RedirectAdapter,
    kill_grace_s: float = 2.0,
) -> InterventionDecision:
    """Route the typed decision onto the matching adapter layer."""

    slot = adapter_slot_for(decision.action)
    target = decision.target_ref
    try:
        if decision.action == "pause":
            assert slot == "pause_resume_adapter"
            ok = bool(pause_resume_adapter.pause(target, reason=decision.reason))
            status: PropagationStatus = "dispatched" if ok else "no_op"
        elif decision.action == "resume":
            assert slot == "pause_resume_adapter"
            ok = bool(pause_resume_adapter.resume(target, reason=decision.reason))
            status = "dispatched" if ok else "no_op"
        elif decision.action == "cancel":
            assert slot == "runtime_adapter"
            ok = bool(runtime_adapter.cancel_turn(target))
            status = "dispatched" if ok else "no_op"
        elif decision.action == "kill":
            assert slot == "runtime_adapter"
            runtime_adapter.kill_switch(kill_grace_s)
            status = "dispatched"
        elif decision.action == "redirect":
            assert slot == "redirect_adapter"
            ok = bool(
                redirect_adapter.cancel_then_reissue(target, reason=decision.reason)
            )
            status = "dispatched" if ok else "no_op"
        else:  # pragma: no cover - exhaustive over INTERVENTION_ACTIONS
            raise ValueError(f"unknown action: {decision.action!r}")
    except Exception:
        return replace(decision, propagation_status="failed")
    return replace(decision, propagation_status=status)


def record_intervention_event(
    decision: InterventionDecision,
    *,
    audit_log: AuditLog,
) -> InterventionDecision:
    """Emit the typed intervention audit event."""

    payload: dict[str, Any] = {
        "action": decision.action,
        "operator_id": decision.operator_id,
        "target_ref": decision.target_ref,
        "reason": decision.reason,
        "issued_at": decision.issued_at.isoformat(),
        "propagation_status": decision.propagation_status,
    }
    audit_event_id = audit_log.emit(
        INTERVENTION_RECORDED_EVENT_TYPE,
        payload,
        trace_id=decision.target_ref or None,
    )
    return replace(decision, audit_event_id=str(audit_event_id or ""))


__all__ = [
    "AuditLog",
    "ApprovalSource",
    "BudgetSnapshot",
    "BudgetSource",
    "CanonicalEventSource",
    "INTERVENTION_ACTIONS",
    "INTERVENTION_RECORDED_EVENT_TYPE",
    "InterventionAction",
    "InterventionDecision",
    "LiveAgentState",
    "PauseResumeAdapter",
    "PROPAGATION_STATUSES",
    "PhaseStatusSource",
    "PropagationStatus",
    "RedirectAdapter",
    "RuntimeAdapter",
    "adapter_slot_for",
    "intervention_capabilities_for",
    "issue_intervention",
    "project_live_agent_state",
    "propagate_intervention",
    "record_intervention_event",
]
