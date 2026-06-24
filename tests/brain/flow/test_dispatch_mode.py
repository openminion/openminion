from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.services import RunnerExecutionServices
from openminion.modules.brain.execution.dispatch import (
    invoke_decision_direct,
    prepare_decision_direct,
)
from openminion.modules.brain.execution.lifecycle import dispatch_execution
from openminion.modules.brain.schemas import (
    BudgetCounters,
    RespondDecision,
    WorkingState,
)


@dataclass
class _FakeServices:
    emitted: list[dict[str, Any]]

    def save_state(self, *, state: WorkingState) -> None:
        del state

    def emit_phase_status(self, *, state: WorkingState, **kwargs) -> None:
        payload = {"state": state}
        payload.update(kwargs)
        self.emitted.append(payload)

    def respond_with_meta(
        self,
        *,
        state: WorkingState,
        logger: Any,
        message: str,
        status: str,
        action_result=None,
    ):
        raise AssertionError("respond_with_meta should not be called in dispatch test")

    def direct_response(self, *, user_input, decision):
        raise AssertionError("direct_response should not be called in dispatch test")


class _Handler:
    def __init__(self, mode_name: str, result: ExecutionResult) -> None:
        self.mode_name = mode_name
        self._result = result

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        del ctx
        return self._result


def _ctx() -> ExecutionContext:
    state = WorkingState(
        session_id="s-dispatch",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=5,
            a2a_calls=5,
            tokens=1000,
            time_ms=10_000,
        ),
    )
    return ExecutionContext(
        state=state,
        decision=RespondDecision(
            confidence=0.9, reason_code="test", answer="hello", respond_kind="answer"
        ),
        user_input="hi",
        logger=SimpleNamespace(emit=lambda *args, **kwargs: None),
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=_FakeServices([]),
    )


@pytest.mark.parametrize(
    ("status", "expected_mode_state", "expected_terminal"),
    [
        ("done", "exited", True),
        ("waiting_user", "exited_waiting", False),
        ("job_pending", "exited_waiting", False),
        ("stopped", "cancelled", True),
        ("error", "failed", True),
        ("failed", "failed", True),
    ],
)
def test_dispatch_execution_emits_expected_exit_mapping(
    status: str,
    expected_mode_state: str,
    expected_terminal: bool,
) -> None:
    ctx = _ctx()
    result = ExecutionResult(status=status, working_state=ctx.state, message="ok")

    dispatch_execution(_Handler("respond", result), ctx)

    emitted = ctx._services.emitted
    assert emitted[0]["source_event"] == "brain.execution.entered"
    assert emitted[0]["mode_state"] == "entered"
    assert emitted[1]["source_event"] == "brain.execution.exited"
    assert emitted[1]["mode_state"] == expected_mode_state
    assert emitted[1]["terminal"] is expected_terminal


def test_dispatch_execution_suppresses_exit_for_active() -> None:
    ctx = _ctx()
    ctx.state.status = "active"
    result = ExecutionResult(status="active", working_state=ctx.state)

    dispatch_execution(_Handler("plan", result), ctx)

    emitted = ctx._services.emitted
    assert len(emitted) == 1
    assert emitted[0]["source_event"] == "brain.execution.entered"


def test_dispatch_execution_suppresses_exit_for_unmapped_status() -> None:
    ctx = _ctx()
    result = ExecutionResult(status="mystery", working_state=ctx.state)

    dispatch_execution(_Handler("respond", result), ctx)

    emitted = ctx._services.emitted
    assert len(emitted) == 1
    assert emitted[0]["source_event"] == "brain.execution.entered"


def test_dispatch_execution_emits_failed_event_on_exception() -> None:
    ctx = _ctx()

    class _BoomHandler:
        mode_name = "plan"

        def execute(self, ctx: ExecutionContext) -> ExecutionResult:
            del ctx
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        dispatch_execution(_BoomHandler(), ctx)

    emitted = ctx._services.emitted
    assert emitted[0]["source_event"] == "brain.execution.entered"
    assert emitted[1]["source_event"] == "brain.execution.failed"
    assert emitted[1]["mode_state"] == "failed"
    assert emitted[1]["terminal"] is True


def test_dispatch_execution_can_suppress_nested_lifecycle_exit_statuses() -> None:
    emitted: list[dict[str, Any]] = []
    runner = SimpleNamespace(
        _emit_phase_status=lambda **kwargs: emitted.append(kwargs),
    )
    state = WorkingState(
        session_id="s-dispatch-nested",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=5,
            a2a_calls=5,
            tokens=1000,
            time_ms=10_000,
        ),
    )
    ctx = ExecutionContext(
        state=state,
        decision=RespondDecision(
            confidence=0.9,
            reason_code="test",
            answer="hello",
            respond_kind="answer",
        ),
        user_input="hi",
        logger=SimpleNamespace(emit=lambda *args, **kwargs: None),
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=RunnerExecutionServices(
            runner=runner,
            suppress_lifecycle_exit_statuses=True,
        ),
    )
    result = ExecutionResult(status="done", working_state=ctx.state, message="ok")

    dispatch_execution(_Handler("respond", result), ctx)

    assert len(emitted) == 1
    assert emitted[0]["source_event"] == "brain.execution.entered"


def test_nested_runner_execution_services_respond_is_ephemeral() -> None:
    append_calls: list[tuple[Any, ...]] = []
    compact_calls: list[dict[str, Any]] = []
    emitted: list[dict[str, Any]] = []
    save_calls: list[WorkingState] = []

    runner = SimpleNamespace(
        _emit_phase_status=lambda **kwargs: emitted.append(kwargs),
        _respond_with_meta=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("_respond_with_meta should not run for nested execution")
        ),
        session_api=SimpleNamespace(
            append_turn=lambda *args, **kwargs: append_calls.append(args)
        ),
        _compact=lambda **kwargs: compact_calls.append(kwargs),
        _save_state=lambda state: save_calls.append(state),
    )
    services = RunnerExecutionServices(
        runner=runner,
        suppress_lifecycle_exit_statuses=True,
    )
    state = WorkingState(
        session_id="s-ephemeral-child",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=5,
            a2a_calls=5,
            tokens=1000,
            time_ms=10_000,
        ),
    )

    output = services.respond_with_meta(
        state=state,
        logger=SimpleNamespace(),
        message="child-result",
        status="waiting_user",
    )

    assert output.status == "waiting_user"
    assert output.message == "child-result"
    assert append_calls == []
    assert compact_calls == []
    assert emitted == []
    assert save_calls == []


def test_invoke_decision_direct_marks_nested_depth_for_exit_suppression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, Any] = {}

    def _fake_direct_owner(**kwargs):
        recorded["suppress_lifecycle_exit_statuses"] = kwargs.get(
            "suppress_lifecycle_exit_statuses"
        )
        return SimpleNamespace(mode_name="respond")

    def _fake_dispatch_owner(owner):
        del owner
        return ExecutionResult(
            status="done", working_state=SimpleNamespace(), message=""
        )

    monkeypatch.setattr(
        "openminion.modules.brain.execution.dispatch._direct_owner",
        _fake_direct_owner,
    )
    monkeypatch.setattr(
        "openminion.modules.brain.execution.dispatch._dispatch_owner",
        _fake_dispatch_owner,
    )

    invoke_decision_direct(
        SimpleNamespace(profile=None),
        state=SimpleNamespace(),
        decision=SimpleNamespace(route="respond"),
        user_input="hi",
        logger=SimpleNamespace(),
        depth=1,
    )

    assert recorded["suppress_lifecycle_exit_statuses"] is True


def test_prepare_decision_direct_keeps_top_level_exit_suppression_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, Any] = {}

    def _fake_direct_owner(**kwargs):
        recorded["suppress_lifecycle_exit_statuses"] = kwargs.get(
            "suppress_lifecycle_exit_statuses"
        )
        return SimpleNamespace(mode_name="respond", prepare_fn=None)

    monkeypatch.setattr(
        "openminion.modules.brain.execution.dispatch._direct_owner",
        _fake_direct_owner,
    )

    assert (
        prepare_decision_direct(
            SimpleNamespace(profile=None),
            state=SimpleNamespace(),
            decision=SimpleNamespace(route="respond"),
            user_input="hi",
            logger=SimpleNamespace(),
        )
        is None
    )
    assert recorded["suppress_lifecycle_exit_statuses"] is False
