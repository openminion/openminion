from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from openminion.modules.brain.bootstrap.payloads import normalize_decision_payload
from openminion.modules.brain.bootstrap.resolve import build_internal_dispatch
from openminion.modules.brain.loop.adaptive import ActLoopMode
from openminion.modules.brain.loop.strategies.research.handler import ResearchMode
from openminion.modules.brain.execution.preflight import ValidationResult
from openminion.modules.brain.execution.loop_contracts import ExecutionResult
from openminion.modules.brain.bootstrap.context import (
    _inject_decide_prompt_contract,
)
from openminion.modules.brain.execution import entry as entry_module
from openminion.modules.brain.execution.entry import (
    build_execution_entry_request,
    dispatch,
)
from openminion.modules.brain.retry import (
    STRUCTURED_RETRY_MESSAGE_HINT,
    add_retry_instruction_to_context,
    build_entry_retry_message,
)
from openminion.modules.brain.schemas import (
    ActionResult,
    ActDecision,
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    BudgetCounters,
    DecisionAdapter,
    ExecutionTargetPayload,
    LLMProfiles,
    RespondDecision,
    StepOutput,
    WorkingState,
)


def _profile(default_act_profile: str | None = None) -> AgentProfile:
    return AgentProfile(
        agent_id="fixed-profile-agent",
        role="general",
        llm_profiles=LLMProfiles(
            decide_model="decide-default",
            plan_model="plan-default",
            act_model=None,
            reflect_model="reflect-default",
            summarize_model="summarize-default",
        ),
        default_act_profile=default_act_profile,
        budgets=AgentBudgets(
            max_ticks_per_user_turn=10,
            max_tool_calls=5,
            max_a2a_calls=3,
            max_total_llm_tokens=5000,
            max_elapsed_ms=30000,
        ),
        defaults=AgentDefaults(),
    )


def _state(session_id: str = "cfg-override") -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="fixed-profile-agent",
        trace_id=f"trace-{session_id}",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=5,
            a2a_calls=3,
            tokens=5000,
            time_ms=120000,
        ),
    )


def _local_target() -> ExecutionTargetPayload:
    return ExecutionTargetPayload(kind="local")


def _runner_for_context(default_act_profile: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        profile=_profile(default_act_profile),
        _configured_agent_ids=["alibaba-kimi-k2-5"],
        agent_registry={"alibaba-kimi-k2-5": object()},
        a2a_api=SimpleNamespace(
            list_agents=lambda: [{"agent_id": "alibaba-kimi-k2-5"}]
        ),
    )


def _retry_message_segment(message: str, prefix: str) -> str:
    for part in message.split(". "):
        if part.startswith(prefix):
            return part
    return ""


def _event_payloads(logger: MagicMock, event_name: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for call in logger.emit.call_args_list:
        if call.args and call.args[0] == event_name:
            payload = call.args[1] if len(call.args) > 1 else {}
            if isinstance(payload, dict):
                payloads.append(payload)
    return payloads


class _FakeRunner:
    def __init__(self, decision: object, *, default_act_profile: str | None) -> None:
        self._decision = decision
        self.profile = _profile(default_act_profile)
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
        del state, user_input, logger, forced_tools, capability_category
        copier = getattr(self._decision, "model_copy", None)
        if callable(copier):
            return copier(deep=True)
        return self._decision

    def _evaluate_meta(self, **kwargs):
        del kwargs
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


class _CapturingDirectDispatchHarness:
    def __init__(self, *, route_internal: bool = False) -> None:
        self.prepare_decisions: list[object] = []
        self.validate_decisions: list[object] = []
        self.invoke_decisions: list[object] = []
        self.route_internal = route_internal
        self.routed_handler: object | None = None
        self.routed_internal_decision: object | None = None

    def prepare(self, **kwargs):
        self.prepare_decisions.append(kwargs["decision"])
        return None

    def validate(self, **kwargs):
        self.validate_decisions.append(kwargs["decision"])
        return ValidationResult(passed=True)

    def invoke(self, *, state, decision, user_input, logger):
        self.invoke_decisions.append(decision)
        if self.route_internal:
            dispatch = build_internal_dispatch(
                SimpleNamespace(
                    state=state,
                    decision=decision,
                    user_input=user_input,
                    logger=logger,
                )
            )
            self.routed_handler = dispatch.handler
            self.routed_internal_decision = dispatch.decision
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
    monkeypatch,
    manager: _CapturingDirectDispatchHarness,
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


@pytest.mark.parametrize(
    "value",
    [None, "", "auto", "AUTO", "general", "coding", "research", "orchestrate"],
)
def test_field_validation_accepts_supported_default_act_profile_values(value) -> None:
    profile = _profile(value)
    expected = None if value in (None, "", "auto", "AUTO") else str(value)
    assert profile.default_act_profile == expected


def test_field_validation_rejects_invalid_default_act_profile() -> None:
    with pytest.raises(ValidationError, match="default_act_profile"):
        _profile("invalid")


def test_normalize_decision_payload_leaves_fixed_profile_for_bootstrap_resolution() -> (
    None
):
    normalized = normalize_decision_payload(
        runner=SimpleNamespace(profile=_profile("coding")),
        raw={
            "mode": "act",
            "confidence": 0.8,
            "reason_code": "tool",
            "sub_intents": [],
            "rationale": "use the act loop",
            "execution_target": {"kind": "local"},
        },
    )

    decision = DecisionAdapter.validate_python(normalized)

    assert decision.mode == "act"
    assert decision.act_profile is None


def test_bootstrap_resolution_overrides_act_profile_before_downstream_consumers(
    monkeypatch,
) -> None:
    runner = _FakeRunner(
        ActDecision(
            confidence=0.9,
            reason_code="llm_pick",
            act_profile="general",
            execution_target=_local_target(),
            rationale="do the research task",
        ),
        default_act_profile="research",
    )
    manager = _CapturingDirectDispatchHarness(route_internal=True)
    _install_direct_dispatch_capture(monkeypatch, manager)
    logger = MagicMock()
    state = _state("cfg-override-injection")

    output = dispatch(
        runner=runner,
        state=state,
        logger=logger,
        request=build_execution_entry_request(
            user_input="research local permits",
            forced_tools=None,
            capability_category=None,
        ),
    )

    assert output.status == "done"
    assert [
        getattr(decision, "act_profile", None)
        for decision in (
            manager.prepare_decisions
            + manager.validate_decisions
            + manager.invoke_decisions
        )
    ] == ["research", "research", "research"]
    assert isinstance(manager.routed_handler, ResearchMode)
    assert getattr(manager.routed_internal_decision, "research_query", "") == (
        "research local permits"
    )
    assert _event_payloads(logger, "brain.decide.profile_override") == []
    assert _event_payloads(logger, "brain.entry")[0]["act_profile"] == "general"
    assert (
        _event_payloads(logger, "brain.entry")[0]["resolved_act_profile"] == "research"
    )
    assert _event_payloads(logger, "brain.act.bootstrap") == [
        {
            "raw_act_profile": "general",
            "raw_execution_target_kind": "local",
            "resolved_act_profile": "research",
            "resolved_execution_target_kind": "local",
            "source": "config_default_act_profile",
        }
    ]


def test_injection_does_not_modify_respond_decision(monkeypatch) -> None:
    runner = _FakeRunner(
        RespondDecision(
            confidence=0.8,
            reason_code="direct_answer",
            respond_kind="answer",
            answer="hello",
        ),
        default_act_profile="coding",
    )
    manager = _CapturingDirectDispatchHarness()
    _install_direct_dispatch_capture(monkeypatch, manager)
    logger = MagicMock()
    state = _state("cfg-override-respond")

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
    assert manager.invoke_decisions[0].mode == "respond"
    assert _event_payloads(logger, "brain.decide.profile_override") == []
    assert _event_payloads(logger, "brain.entry")[0]["act_profile"] is None


@pytest.mark.parametrize("default_act_profile", [None, "auto"])
def test_injection_preserves_llm_choice_when_profile_is_auto_or_none(
    monkeypatch,
    default_act_profile: str | None,
) -> None:
    runner = _FakeRunner(
        ActDecision(
            confidence=0.9,
            reason_code="llm_pick",
            act_profile="general",
            execution_target=_local_target(),
            rationale="use the general loop",
        ),
        default_act_profile=default_act_profile,
    )
    manager = _CapturingDirectDispatchHarness(route_internal=True)
    _install_direct_dispatch_capture(monkeypatch, manager)
    logger = MagicMock()
    state = _state(f"cfg-override-auto-{default_act_profile or 'none'}")

    output = dispatch(
        runner=runner,
        state=state,
        logger=logger,
        request=build_execution_entry_request(
            user_input="what time is it",
            forced_tools=None,
            capability_category=None,
        ),
    )

    assert output.status == "done"
    assert manager.invoke_decisions[0].act_profile == "general"
    assert isinstance(manager.routed_handler, ActLoopMode)
    assert _event_payloads(logger, "brain.decide.profile_override") == []
    assert _event_payloads(logger, "brain.entry")[0]["act_profile"] == "general"


def test_prompt_suppression_fixed_profile_rewrites_profile_teaching_rules() -> None:
    hints: dict[str, object] = {}

    _inject_decide_prompt_contract(hints, runner=_runner_for_context("research"))

    style_overrides = hints["style_overrides"]
    assert isinstance(style_overrides, dict)
    assert hints["default_act_profile"] == "research"
    for key in (
        "entry_response_rule",
        "entry_tool_rule",
        "entry_clarify_rule",
        "entry_no_routing_metadata_rule",
        "entry_text_answer_rule",
        "entry_fixed_profile_rule",
    ):
        assert str(style_overrides.get(key, "")).strip()
    assert "submit_output" in str(style_overrides["entry_no_routing_metadata_rule"])
    assert "act_profile" in str(style_overrides["entry_no_routing_metadata_rule"])
    assert "Runtime already resolved the working act profile to 'research'" in str(
        style_overrides["entry_fixed_profile_rule"]
    )


def test_prompt_suppression_auto_profile_preserves_current_rules() -> None:
    hints: dict[str, object] = {}

    _inject_decide_prompt_contract(hints, runner=_runner_for_context("auto"))

    style_overrides = hints["style_overrides"]
    assert isinstance(style_overrides, dict)
    assert "default_act_profile" not in hints
    assert "entry_response_rule" in style_overrides
    assert "entry_fixed_profile_rule" not in style_overrides
    assert "submit_output" in str(style_overrides["entry_no_routing_metadata_rule"])


def test_retry_suppression_fixed_profile_removes_profile_selection_guidance() -> None:
    context = add_retry_instruction_to_context(
        context={"hints": {"default_act_profile": "research"}},
        schema_name="Decision",
        schema=DecisionAdapter,
    )
    message = context["hints"][STRUCTURED_RETRY_MESSAGE_HINT]

    assert "act_profile/execution_target" not in message
    assert "act_profile='orchestrate'" not in message
    assert "runtime assigns act_profile from config" in message
    assert "act_profile" not in _retry_message_segment(message, "Schema keys:")
    assert "act_profile" not in _retry_message_segment(message, "Required schema keys:")

    entry_message = build_entry_retry_message(has_real_tools=True)
    assert "clarify(question=...)" in entry_message
    assert "submit_output" in entry_message
    assert "act_profile" in entry_message


def test_retry_suppression_auto_profile_preserves_guidance() -> None:
    context = add_retry_instruction_to_context(
        context={"hints": {}},
        schema_name="Decision",
        schema=DecisionAdapter,
    )
    message = context["hints"][STRUCTURED_RETRY_MESSAGE_HINT]

    assert "act_profile/execution_target" in message
    assert "act_profile='orchestrate'" in message


def test_characterization_fixed_research_profile_routes_to_research_handler(
    monkeypatch,
) -> None:
    runner = _FakeRunner(
        ActDecision(
            confidence=0.9,
            reason_code="llm_pick",
            act_profile="general",
            execution_target=_local_target(),
            rationale="research the latest update",
        ),
        default_act_profile="research",
    )
    manager = _CapturingDirectDispatchHarness(route_internal=True)
    _install_direct_dispatch_capture(monkeypatch, manager)
    state = _state("cfg-override-characterization")

    output = dispatch(
        runner=runner,
        state=state,
        logger=MagicMock(),
        request=build_execution_entry_request(
            user_input="research the latest update",
            forced_tools=None,
            capability_category=None,
        ),
    )

    assert output.status == "done"
    assert isinstance(manager.routed_handler, ResearchMode)
    assert getattr(manager.routed_internal_decision, "research_query", "") == (
        "research the latest update"
    )
