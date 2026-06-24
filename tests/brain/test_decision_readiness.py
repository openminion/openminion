from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from openminion.modules.brain.constants import BRAIN_STATE_WAITING_USER
from openminion.modules.brain.execution import entry as entry_module
from openminion.modules.brain.execution.entry import (
    build_execution_entry_request,
    dispatch,
)
from openminion.modules.brain.execution.loop_contracts import ExecutionResult
from openminion.modules.brain.execution.preflight import ValidationResult
from openminion.modules.brain.schemas import (
    ActionResult,
    AgentCommand,
    BudgetCounters,
    ClarifyContext,
    RespondDecision,
    StepOutput,
    ToolCommand,
    WorkingState,
)
from tests.brain.runner_test_support import _profile, build_seeded_act_decision


class _FakeRunner:
    def __init__(self, decisions: list[object]) -> None:
        self._decisions = list(decisions)
        self.decide_constraints: list[list[str]] = []
        self.meta_user_inputs: list[object] = []
        self.profile = _profile()
        self.options = SimpleNamespace(plan_checkpoint_interval=0)

    def _decide(
        self,
        *,
        state,
        user_input,
        logger,
        forced_tools=None,
        capability_category=None,
    ):
        del user_input, logger, forced_tools, capability_category
        self.decide_constraints.append(list(getattr(state, "constraints", []) or []))
        next_decision = self._decisions.pop(0)
        return next_decision

    def _evaluate_meta(self, **kwargs):
        self.meta_user_inputs.append(kwargs.get("user_input"))
        return None

    def _respond_with_meta(
        self,
        *,
        state,
        logger,
        message,
        status,
        action_result=None,
    ) -> StepOutput:
        del logger
        return StepOutput(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
        )


class _FakeDirectDispatchHarness:
    def __init__(self, *, validate_result: ValidationResult | None = None) -> None:
        self.prepare_calls = 0
        self.validate_calls: list[object] = []
        self.invoke_calls: list[object] = []
        self.prepare_user_inputs: list[object] = []
        self.validate_user_inputs: list[object] = []
        self.invoke_user_inputs: list[object] = []
        self._validate_result = validate_result or ValidationResult(passed=True)

    def prepare(self, **kwargs):
        self.prepare_calls += 1
        self.prepare_user_inputs.append(kwargs.get("user_input"))
        return None

    def validate(self, **kwargs):
        self.validate_calls.append(kwargs["decision"])
        self.validate_user_inputs.append(kwargs.get("user_input"))
        return self._validate_result

    def invoke(self, *, state, decision, user_input, logger):
        del logger
        self.invoke_calls.append(decision)
        self.invoke_user_inputs.append(user_input)
        return ExecutionResult(
            status="done",
            working_state=state,
            message="done",
            action_result=ActionResult(
                command_id="cmd-1",
                status="success",
                summary="done",
            ),
        )


def _install_direct_dispatch_capture(
    monkeypatch, manager: _FakeDirectDispatchHarness
) -> None:
    monkeypatch.setattr(
        entry_module,
        "prepare_decision_direct",
        lambda runner, *, state, decision, user_input, logger, emit_status_updates=False: (
            manager.prepare(
                runner=runner,
                state=state,
                decision=decision,
                user_input=user_input,
                logger=logger,
                emit_status_updates=emit_status_updates,
            )
        ),
    )
    monkeypatch.setattr(
        entry_module,
        "validate_decision_direct",
        lambda runner, *, state, decision, user_input, logger, preparation=None: (
            manager.validate(
                runner=runner,
                state=state,
                decision=decision,
                user_input=user_input,
                logger=logger,
                preparation=preparation,
            )
        ),
    )
    monkeypatch.setattr(
        entry_module,
        "invoke_decision_direct",
        lambda runner, *, state, decision, user_input, logger: manager.invoke(
            state=state,
            decision=decision,
            user_input=user_input,
            logger=logger,
        ),
    )


def _state(*, session_id: str = "s-decision-readiness") -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="router-agent",
        trace_id=f"trace-{session_id}",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=5,
            a2a_calls=5,
            tokens=5000,
            time_ms=120000,
        ),
    )


def _event_types(logger: MagicMock) -> list[str]:
    return [call.args[0] for call in logger.emit.call_args_list]


def test_dispatch_redecides_before_mode_validation_on_placeholder_tool_args(
    monkeypatch,
) -> None:
    state = _state(session_id="s-placeholder-readiness")
    runner = _FakeRunner(
        [
            build_seeded_act_decision(
                confidence=0.9,
                reason_code="check_weather_invalid",
                act_profile="general",
                execution_target={"kind": "local"},
                sub_intents=[],
                rationale="Check the weather.",
                command=ToolCommand(
                    title="Check weather",
                    tool_name="weather.current",
                    args={"location": "<UNKNOWN>"},
                ),
            ),
            build_seeded_act_decision(
                confidence=0.9,
                reason_code="check_weather_valid",
                act_profile="general",
                execution_target={"kind": "local"},
                sub_intents=[],
                rationale="Check the weather.",
                command=ToolCommand(
                    title="Check weather",
                    tool_name="weather.current",
                    args={"location": "Beijing"},
                ),
            ),
        ]
    )
    manager = _FakeDirectDispatchHarness()
    _install_direct_dispatch_capture(monkeypatch, manager)
    logger = MagicMock()

    output = dispatch(
        runner=runner,
        state=state,
        logger=logger,
        request=build_execution_entry_request(
            user_input="weather in beijing",
            forced_tools=None,
            capability_category=None,
        ),
    )

    assert output.status == "done"
    assert len(runner.decide_constraints) == 2
    assert runner.decide_constraints[0] == []
    assert "DECISION_VALIDATION_FEEDBACK:" in runner.decide_constraints[1][0]
    assert "_seeded_commands[0].args.location" in runner.decide_constraints[1][0]
    assert "<UNKNOWN>" in runner.decide_constraints[1][0]
    assert manager.prepare_calls == 1
    assert len(manager.validate_calls) == 1
    assert len(manager.invoke_calls) == 1
    assert _event_types(logger).count("brain.entry.validation_failed") == 1
    assert _event_types(logger).count("brain.entry.validation_redecide") == 1


def test_dispatch_consumes_confirmation_reply_before_direct_execution(
    monkeypatch,
) -> None:
    state = _state(session_id="s-confirmation-replay-consume")
    decision = build_seeded_act_decision(
        confidence=1.0,
        reason_code="confirmation_replay",
        act_profile="general",
        execution_target={"kind": "local"},
        sub_intents=["create_scratch_project"],
        rationale="Replay the previously confirmed tool command.",
        command=ToolCommand(
            title="Write project file",
            tool_name="file.write",
            args={"path": "/tmp/demo.txt", "content": "demo"},
        ),
    )
    runner = _FakeRunner([decision])
    manager = _FakeDirectDispatchHarness()
    _install_direct_dispatch_capture(monkeypatch, manager)
    logger = MagicMock()

    output = dispatch(
        runner=runner,
        state=state,
        logger=logger,
        request=build_execution_entry_request(
            user_input="yes",
            forced_tools=None,
            capability_category=None,
            skip_decide=True,
            decision=decision,
            consume_user_input_for_command=True,
        ),
    )

    assert output.status == "done"
    assert manager.prepare_user_inputs == [None]
    assert manager.validate_user_inputs == [None]
    assert manager.invoke_user_inputs == [None]


def test_dispatch_consumes_confirmation_reply_before_meta_and_route_resolution(
    monkeypatch,
) -> None:
    state = _state(session_id="s-confirmation-replay-route")
    decision = build_seeded_act_decision(
        confidence=1.0,
        reason_code="confirmation_replay",
        act_profile="general",
        execution_target={"kind": "local"},
        sub_intents=["create_scratch_project"],
        rationale="Replay the previously confirmed tool command.",
        command=ToolCommand(
            title="Write project file",
            tool_name="file.write",
            args={"path": "/tmp/demo.txt", "content": "demo"},
        ),
    )
    runner = _FakeRunner([decision])
    manager = _FakeDirectDispatchHarness()
    _install_direct_dispatch_capture(monkeypatch, manager)
    logger = MagicMock()
    route_flags: list[bool] = []
    original_apply_route = entry_module.apply_resolved_act_route

    def _capture_route(*, decision, state, default_act_profile, has_new_user_input):
        del state, default_act_profile
        route_flags.append(has_new_user_input)
        return SimpleNamespace(
            act_profile="general",
            execution_target=SimpleNamespace(kind="local"),
            source="test_capture",
        )

    def _apply_route_with_marker(*, decision, route):
        routed = original_apply_route(decision=decision, route=route)
        setattr(routed, "_pre_resolved_act_route", route)
        return routed

    monkeypatch.setattr(entry_module, "resolve_working_act_route", _capture_route)
    monkeypatch.setattr(
        entry_module, "apply_resolved_act_route", _apply_route_with_marker
    )

    output = dispatch(
        runner=runner,
        state=state,
        logger=logger,
        request=build_execution_entry_request(
            user_input="yes",
            forced_tools=None,
            capability_category=None,
            skip_decide=True,
            decision=decision,
            consume_user_input_for_command=True,
        ),
    )

    assert output.status == "done"
    assert runner.meta_user_inputs == [None]
    assert route_flags == [False, False]


def test_dispatch_redecides_on_post_clarify_empty_payload_with_context_summary(
    monkeypatch,
) -> None:
    state = _state(session_id="s-post-clarify-readiness")
    state.status = BRAIN_STATE_WAITING_USER
    state.pending_llm_clarify_context = ClarifyContext(
        original_user_input="what's weather?",
        inferred_goal="weather",
        known_context={"place": "China"},
        unresolved_question="Which city?",
        clarify_question="Which city?",
    )
    runner = _FakeRunner(
        [
            build_seeded_act_decision(
                confidence=0.9,
                reason_code="clarify_followup_invalid",
                act_profile="general",
                execution_target={"kind": "local"},
                sub_intents=[],
                rationale="Check the weather.",
                command=ToolCommand(
                    title="Check weather",
                    tool_name="weather.current",
                    args={},
                ),
            ),
            build_seeded_act_decision(
                confidence=0.9,
                reason_code="clarify_followup_valid",
                act_profile="general",
                execution_target={"kind": "local"},
                sub_intents=[],
                rationale="Check the weather.",
                command=ToolCommand(
                    title="Check weather",
                    tool_name="weather.current",
                    args={"location": "Beijing"},
                ),
            ),
        ]
    )
    manager = _FakeDirectDispatchHarness()
    _install_direct_dispatch_capture(monkeypatch, manager)
    logger = MagicMock()

    output = dispatch(
        runner=runner,
        state=state,
        logger=logger,
        request=build_execution_entry_request(
            user_input="china",
            forced_tools=None,
            capability_category=None,
        ),
    )

    assert output.status == "done"
    assert len(runner.decide_constraints) == 2
    feedback = runner.decide_constraints[1][0]
    assert "DECISION_VALIDATION_FEEDBACK:" in feedback
    assert "_seeded_commands[0].args" in feedback
    assert "original_user_input=" in feedback
    assert "what's weather?" in feedback
    assert "known_context={place='China'}" in feedback
    assert "clarify_question='Which city?'" in feedback
    assert len(manager.validate_calls) == 1
    assert output.working_state.pending_llm_clarify_context is None
    assert _event_types(logger).count("brain.entry.validation_failed") == 1


def test_dispatch_contextual_gate_does_not_fire_without_pending_clarify_context(
    monkeypatch,
) -> None:
    state = _state(session_id="s-no-context-readiness")
    runner = _FakeRunner(
        [
            build_seeded_act_decision(
                confidence=0.9,
                reason_code="empty_payload_but_no_context",
                act_profile="general",
                execution_target={"kind": "local"},
                sub_intents=[],
                rationale="Do the work.",
                command=ToolCommand(
                    title="Zero arg tool",
                    tool_name="time.now",
                    args={},
                ),
            )
        ]
    )
    manager = _FakeDirectDispatchHarness()
    _install_direct_dispatch_capture(monkeypatch, manager)
    logger = MagicMock()

    output = dispatch(
        runner=runner,
        state=state,
        logger=logger,
        request=build_execution_entry_request(
            user_input="time",
            forced_tools=None,
            capability_category=None,
        ),
    )

    assert output.status == "done"
    assert len(runner.decide_constraints) == 1
    assert manager.prepare_calls == 1
    assert len(manager.validate_calls) == 1
    assert len(manager.invoke_calls) == 1
    assert "brain.entry.validation_failed" not in _event_types(logger)


def test_dispatch_non_act_modes_skip_readiness_gate(monkeypatch) -> None:
    state = _state(session_id="s-respond-skip-readiness")
    runner = _FakeRunner(
        [
            RespondDecision(
                confidence=0.9,
                reason_code="answer_directly",
                respond_kind="answer",
                answer="Hi there.",
            )
        ]
    )
    manager = _FakeDirectDispatchHarness()
    _install_direct_dispatch_capture(monkeypatch, manager)
    logger = MagicMock()

    output = dispatch(
        runner=runner,
        state=state,
        logger=logger,
        request=build_execution_entry_request(
            user_input="hi",
            forced_tools=None,
            capability_category=None,
        ),
    )

    assert output.status == "done"
    assert len(runner.decide_constraints) == 1
    assert manager.prepare_calls == 1
    assert len(manager.validate_calls) == 1
    assert len(manager.invoke_calls) == 1
    assert "brain.entry.validation_failed" not in _event_types(logger)


def test_dispatch_baseline_readiness_checks_agent_params_and_redecides(
    monkeypatch,
) -> None:
    state = _state(session_id="s-agent-readiness")
    runner = _FakeRunner(
        [
            build_seeded_act_decision(
                confidence=0.9,
                reason_code="delegate_invalid",
                act_profile="general",
                execution_target={"kind": "local"},
                sub_intents=[],
                rationale="Delegate a search.",
                command=AgentCommand(
                    title="Delegate search",
                    target_agent_id="search-agent",
                    method="search",
                    params={"query": "<UNKNOWN>"},
                ),
            ),
            build_seeded_act_decision(
                confidence=0.9,
                reason_code="delegate_valid",
                act_profile="general",
                execution_target={"kind": "local"},
                sub_intents=[],
                rationale="Delegate a search.",
                command=AgentCommand(
                    title="Delegate search",
                    target_agent_id="search-agent",
                    method="search",
                    params={"query": "valid text"},
                ),
            ),
        ]
    )
    manager = _FakeDirectDispatchHarness()
    _install_direct_dispatch_capture(monkeypatch, manager)
    logger = MagicMock()

    output = dispatch(
        runner=runner,
        state=state,
        logger=logger,
        request=build_execution_entry_request(
            user_input="delegate a search",
            forced_tools=None,
            capability_category=None,
        ),
    )

    assert output.status == "done"
    assert len(runner.decide_constraints) == 2
    assert "_seeded_commands[0].params.query" in runner.decide_constraints[1][0]
    assert len(manager.validate_calls) == 1
