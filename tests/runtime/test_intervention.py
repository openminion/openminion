from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from typing import Any, get_args

import pytest

from openminion.modules.runtime.intervention import (
    INTERVENTION_ACTIONS,
    INTERVENTION_RECORDED_EVENT_TYPE,
    BudgetSnapshot,
    InterventionAction,
    InterventionDecision,
    LiveAgentState,
    PropagationStatus,
    adapter_slot_for,
    issue_intervention,
    project_live_agent_state,
    propagate_intervention,
    record_intervention_event,
)


def test_intervention_action_literal_is_exhaustive_five_values() -> None:
    assert set(get_args(InterventionAction)) == {
        "pause",
        "resume",
        "cancel",
        "kill",
        "redirect",
    }
    assert len(get_args(InterventionAction)) == 5


def test_intervention_actions_tuple_matches_literal() -> None:
    assert tuple(get_args(InterventionAction)) == INTERVENTION_ACTIONS


def test_propagation_status_literal_is_closed_set() -> None:
    assert set(get_args(PropagationStatus)) == {
        "pending",
        "dispatched",
        "no_op",
        "failed",
    }


def test_issue_intervention_rejects_unknown_action() -> None:
    with pytest.raises(ValueError):
        issue_intervention(
            "auto_pause",  # type: ignore[arg-type]
            operator_id="op-1",
            target_ref="trace-1",
            reason="unused",
        )


class _StaticStatusSource:
    def __init__(self, status_key: str) -> None:
        self._status_key = status_key

    def current_phase_status(self, trace_id: str) -> dict[str, Any]:
        return {"trace_id": trace_id, "status_key": self._status_key}


class _StaticEventSource:
    def __init__(
        self,
        *,
        last_event_type: str,
        pending_tool_calls: tuple[str, ...] = (),
    ) -> None:
        self._last = last_event_type
        self._pending = pending_tool_calls

    def latest_event_type(self, trace_id: str) -> str:
        return self._last

    def pending_tool_calls(self, trace_id: str) -> tuple[str, ...]:
        return self._pending


class _StaticApprovalSource:
    def __init__(self, pending: tuple[str, ...] = ()) -> None:
        self._pending = pending

    def pending_approvals(self, trace_id: str) -> tuple[str, ...]:
        return self._pending


class _StaticBudgetSource:
    def __init__(self, snapshot: BudgetSnapshot) -> None:
        self._snapshot = snapshot

    def budget_snapshot(self, trace_id: str) -> BudgetSnapshot:
        return self._snapshot


def _make_projection(
    *,
    status_key: str = "executing",
    last_event_type: str = "tool.invoked",
    pending_tool_calls: tuple[str, ...] = ("shell",),
    pending_approvals: tuple[str, ...] = (),
    lifecycle_phase: str = "running",
) -> LiveAgentState:
    snapshot = BudgetSnapshot(llm_calls_remaining=5, input_tokens_remaining=1000)
    return project_live_agent_state(
        "trace-1",
        agent_id="agent-1",
        lifecycle_phase=lifecycle_phase,
        status_source=_StaticStatusSource(status_key),
        event_source=_StaticEventSource(
            last_event_type=last_event_type,
            pending_tool_calls=pending_tool_calls,
        ),
        approval_source=_StaticApprovalSource(pending_approvals),
        budget_source=_StaticBudgetSource(snapshot),
    )


def test_projection_is_deterministic_same_inputs_same_output() -> None:
    a = _make_projection()
    b = _make_projection()
    assert a == b


def test_projection_reads_typed_primitives_only_no_prose_scanning() -> None:
    state = _make_projection(status_key="executing", last_event_type="tool.invoked")
    assert state.phase == "executing"
    assert state.last_event_type == "tool.invoked"


def test_live_agent_state_has_exactly_nine_fields() -> None:
    expected = {
        "trace_id",
        "agent_id",
        "phase",
        "lifecycle_phase",
        "pending_tool_calls",
        "pending_approvals",
        "budget_remaining",
        "last_event_type",
        "intervention_capabilities",
    }
    assert set(LiveAgentState.__dataclass_fields__.keys()) == expected
    assert len(LiveAgentState.__dataclass_fields__) == 9


def test_live_agent_state_is_frozen() -> None:
    state = _make_projection()
    with pytest.raises(FrozenInstanceError):
        state.phase = "tampered"  # type: ignore[misc]


def test_capabilities_reflect_lifecycle_phase_paused_only_resume_cancel_kill() -> None:
    state = _make_projection(lifecycle_phase="paused")
    assert state.intervention_capabilities == ("resume", "cancel", "kill")


def test_capabilities_running_offers_pause_cancel_kill_redirect() -> None:
    state = _make_projection(lifecycle_phase="running")
    assert state.intervention_capabilities == ("pause", "cancel", "kill", "redirect")


def test_capabilities_terminal_offers_nothing() -> None:
    state = _make_projection(
        status_key="completed",
        last_event_type="",
        pending_tool_calls=(),
        lifecycle_phase="completed",
    )
    assert state.intervention_capabilities == ()


class _RecordingRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def cancel_turn(self, trace_id: str) -> bool:
        self.calls.append(("cancel_turn", trace_id))
        return True

    def kill_switch(self, grace_s: float = 2.0) -> None:
        self.calls.append(("kill_switch", grace_s))


class _RecordingPauseResume:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def pause(self, trace_id: str, *, reason: str) -> bool:
        self.calls.append(("pause", trace_id, reason))
        return True

    def resume(self, trace_id: str, *, reason: str) -> bool:
        self.calls.append(("resume", trace_id, reason))
        return True


class _RecordingRedirect:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def cancel_then_reissue(self, trace_id: str, *, reason: str) -> bool:
        self.calls.append(("cancel_then_reissue", trace_id, reason))
        return True


class _RecordingAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any], str | None]] = []
        self._next_id = 0

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        trace_id: str | None = None,
    ) -> str:
        self._next_id += 1
        event_id = f"evt-{self._next_id}"
        self.events.append((event_type, dict(payload), trace_id))
        return event_id


def _recording_adapters() -> tuple[
    _RecordingRuntime,
    _RecordingPauseResume,
    _RecordingRedirect,
]:
    return _RecordingRuntime(), _RecordingPauseResume(), _RecordingRedirect()


def _propagate(action: InterventionAction, *, runtime, pause_resume, redirect):
    decision = issue_intervention(
        action,
        operator_id="op-7",
        target_ref="trace-1",
        reason=f"{action} requested",
        issued_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    return propagate_intervention(
        decision,
        runtime_adapter=runtime,
        pause_resume_adapter=pause_resume,
        redirect_adapter=redirect,
    )


def test_each_action_reaches_exactly_one_adapter_slot() -> None:
    for action in INTERVENTION_ACTIONS:
        runtime, pause_resume, redirect = _recording_adapters()

        decision = _propagate(
            action,
            runtime=runtime,
            pause_resume=pause_resume,
            redirect=redirect,
        )

        all_calls = (
            [("runtime", *c) for c in runtime.calls]
            + [("pause_resume", *c) for c in pause_resume.calls]
            + [("redirect", *c) for c in redirect.calls]
        )
        assert len(all_calls) == 1, f"action {action!r} should hit exactly one adapter"
        assert decision.propagation_status == "dispatched"


def test_adapter_slot_mapping_is_frozen_and_exhaustive() -> None:
    keys = {a for a in INTERVENTION_ACTIONS}
    mapped = {a: adapter_slot_for(a) for a in INTERVENTION_ACTIONS}
    assert set(mapped.keys()) == keys
    assert set(mapped.values()) <= {
        "runtime_adapter",
        "pause_resume_adapter",
        "redirect_adapter",
    }


def test_adapter_slot_for_pause_and_resume_share_slot() -> None:
    assert adapter_slot_for("pause") == adapter_slot_for("resume")


def test_adapter_slot_for_cancel_and_kill_share_runtime_slot() -> None:
    assert adapter_slot_for("cancel") == "runtime_adapter"
    assert adapter_slot_for("kill") == "runtime_adapter"


def test_redirect_is_cancel_then_reissue_never_inplace_rewrite() -> None:
    runtime, pause_resume, redirect = _recording_adapters()

    _propagate(
        "redirect",
        runtime=runtime,
        pause_resume=pause_resume,
        redirect=redirect,
    )

    assert redirect.calls == [("cancel_then_reissue", "trace-1", "redirect requested")]
    assert runtime.calls == []
    assert pause_resume.calls == []


def test_redirect_adapter_protocol_exposes_cancel_then_reissue_only() -> None:
    from openminion.modules.runtime.intervention import RedirectAdapter

    members = {name for name in dir(RedirectAdapter) if not name.startswith("_")}
    assert "cancel_then_reissue" in members
    forbidden = {"rewrite", "mutate", "inject_prompt", "edit_in_place"}
    assert members.isdisjoint(forbidden)


def test_intervention_decision_field_names_are_structural() -> None:
    fields = set(InterventionDecision.__dataclass_fields__.keys())
    assert fields == {
        "action",
        "operator_id",
        "target_ref",
        "reason",
        "issued_at",
        "propagation_status",
        "audit_event_id",
    }
    forbidden = {
        "llm_verdict",
        "model_judgement",
        "auto_detected",
        "prose_summary",
        "stuck_score",
    }
    assert fields.isdisjoint(forbidden)


def test_live_agent_state_field_names_are_structural() -> None:
    fields = set(LiveAgentState.__dataclass_fields__.keys())
    forbidden = {
        "looks_stuck",
        "model_summary",
        "transcript_excerpt",
        "llm_diagnosis",
    }
    assert fields.isdisjoint(forbidden)


def test_decision_to_audit_event_parity_across_all_actions() -> None:
    audit = _RecordingAudit()
    runtime, pause_resume, redirect = _recording_adapters()

    decisions: list[InterventionDecision] = []
    for action in INTERVENTION_ACTIONS:
        decision = _propagate(
            action,
            runtime=runtime,
            pause_resume=pause_resume,
            redirect=redirect,
        )
        recorded = record_intervention_event(decision, audit_log=audit)
        decisions.append(recorded)

    assert len(decisions) == len(INTERVENTION_ACTIONS)
    assert len(audit.events) == len(decisions)
    assert all(d.audit_event_id.startswith("evt-") for d in decisions)
    for decision, (event_type, payload, _trace) in zip(decisions, audit.events):
        assert event_type == INTERVENTION_RECORDED_EVENT_TYPE
        assert payload["action"] == decision.action
        assert payload["operator_id"] == decision.operator_id


def test_audit_event_id_is_emitter_assigned_not_prose_derived() -> None:
    audit = _RecordingAudit()
    decision = issue_intervention(
        "cancel",
        operator_id="op-1",
        target_ref="trace-99",
        reason="user cancel",
    )
    recorded = record_intervention_event(decision, audit_log=audit)
    assert recorded.audit_event_id == "evt-1"
    assert recorded.audit_event_id != decision.reason


class _FailingRuntime:
    def cancel_turn(self, trace_id: str) -> bool:
        raise RuntimeError("boom")

    def kill_switch(self, grace_s: float = 2.0) -> None:
        raise RuntimeError("boom")


def test_propagation_failure_records_failed_status() -> None:
    decision = issue_intervention(
        "cancel",
        operator_id="op-1",
        target_ref="trace-1",
        reason="x",
    )
    propagated = propagate_intervention(
        decision,
        runtime_adapter=_FailingRuntime(),
        pause_resume_adapter=_RecordingPauseResume(),
        redirect_adapter=_RecordingRedirect(),
    )
    assert propagated.propagation_status == "failed"


def test_issue_does_not_dispatch() -> None:
    runtime = _RecordingRuntime()
    issue_intervention(
        "cancel",
        operator_id="op-1",
        target_ref="trace-1",
        reason="x",
    )
    assert runtime.calls == []
