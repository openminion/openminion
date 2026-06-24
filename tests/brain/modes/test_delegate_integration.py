from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from openminion.modules.brain.bootstrap.route_catalog import (
    available_routes,
    get_route_descriptor,
)
from openminion.modules.brain.constants import (
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
)
from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.execution.targets.delegated.handler import DelegateMode
from openminion.modules.brain.execution.targets.delegated.strategies import (
    HashKeyGenerator,
    PassThroughSynthesizer,
)
from openminion.modules.brain.execution.dispatch import invoke_decision_direct
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.brain.schemas import (
    ActionError,
    ActionResult,
    AgentProfile,
    BudgetCounters,
    ModeProfileConfig,
    WorkingState,
)
from openminion.modules.brain.schemas.decisions import DecisionAdapter
from tests.brain.runner_test_support import _profile


@dataclass
class _FakeRunner:
    profile: AgentProfile
    agent_registry: Any


@dataclass
class _FakeServices:
    runner: _FakeRunner
    statuses: list[dict[str, Any]]
    action_result: ActionResult
    command_calls: list[Any]

    def save_state(self, *, state: WorkingState) -> None:
        del state

    def emit_phase_status(self, *, state: WorkingState, **kwargs) -> None:
        del state
        self.statuses.append(dict(kwargs))

    def respond_with_meta(
        self,
        *,
        state: WorkingState,
        logger: Any,
        message: str,
        status: str,
        action_result: ActionResult | None = None,
        kind: str = "assistant",
    ):
        del logger, kind
        state.status = status
        if action_result is not None:
            state.last_result = action_result
        return SimpleNamespace(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
        )

    def direct_response(self, *, user_input, decision):
        del user_input, decision
        return ""

    def plan(self, *, state, user_input, logger, decision=None):
        del state, user_input, logger, decision
        raise AssertionError("delegate mode should not call ctx.plan()")

    def approve_command(self, *, state, command, logger):
        del state, logger
        return command

    def act_command(self, *, state, command, logger):
        del state, logger
        self.command_calls.append(command)
        return self.action_result, None

    def assess_plan_feasibility(self, *, state, user_input, logger):
        del state, user_input, logger
        return None

    def evaluate_meta(self, **kwargs):
        del kwargs
        return None

    def apply_meta_directive(self, **kwargs):
        del kwargs

    def meta_override_response(self, **kwargs):
        del kwargs
        return None

    def meta_tool_restriction_reason(self, *, command, directive):
        del command, directive
        return None

    def command_has_side_effects(self, *, command):
        del command
        return True

    def resolve_verification_mode(self, *, current, candidate):
        return candidate if candidate is not None else current

    def verify(self, *, state, command, action_result, mode, logger):
        del state, command, action_result, mode, logger
        return True

    def improve(self, *, state, report, logger):
        del state, report, logger

    def compact(self, *, state, logger, content=""):
        del state, logger, content

    def evaluate_turn_closure(self, **kwargs):
        del kwargs
        return None

    def apply_closure_judgment(self, *, state, judgment):
        del state, judgment
        return "close"

    def extract_success_memories(self, **kwargs):
        del kwargs
        return []


def _state() -> WorkingState:
    return WorkingState(
        session_id="s-delegate",
        agent_id="router-agent",
        goal="Delegate weather request",
        budgets_remaining=BudgetCounters(
            ticks=8,
            tool_calls=5,
            a2a_calls=2,
            tokens=5000,
            time_ms=120000,
        ),
        trace_id="trace-delegate",
    )


def _ctx(
    *,
    registry_data: Any,
    action_result: ActionResult | None = None,
    synthesize_result: bool = False,
):
    services = _FakeServices(
        runner=_FakeRunner(profile=_profile(), agent_registry=registry_data),
        statuses=[],
        action_result=action_result
        or ActionResult(
            command_id="cmd-delegate",
            status="success",
            summary="Delegate completed cleanly.",
            outputs={"answer": "weather is sunny"},
        ),
        command_calls=[],
    )
    decision = SimpleNamespace(
        mode="delegate",
        confidence=0.9,
        reason_code="delegate_specialist",
        target_agent_id="agent.weather",
        target_capability=None,
        goal="Check the weather in San Francisco",
        constraints="be concise",
        synthesize_result=synthesize_result,
        timeout_ms=2500,
        sub_intents=[],
        rationale="",
        question=None,
        answer=None,
    )
    logger = SimpleNamespace(
        events=[],
        emit=lambda event_type, payload, **kwargs: logger.events.append(
            {"type": event_type, "payload": payload, "kwargs": kwargs}
        ),
    )
    ctx = ExecutionContext(
        state=_state(),
        decision=decision,
        user_input="what's the weather in san francisco?",
        logger=logger,
        options=SimpleNamespace(decompose_cancel_requested=False),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=services,
    )
    return ctx, services, logger


def test_delegate_handler_executes_success_path_and_emits_statuses() -> None:
    ctx, services, _logger = _ctx(registry_data={"agent.weather": {"state": "healthy"}})

    result = DelegateMode().execute(ctx)

    assert result.status == "done"
    assert result.message == "Delegate completed cleanly."
    assert len(services.command_calls) == 1
    command = services.command_calls[0]
    assert command.target_agent_id == "agent.weather"
    assert command.method == "delegate"
    assert command.timeout_ms == 2500
    assert command.params["goal"] == "Check the weather in San Francisco"
    assert command.params["constraints"] == ["be concise"]
    assert command.idempotency_key == HashKeyGenerator().generate(
        session_id=ctx.state.session_id,
        trace_id=ctx.state.trace_id,
        goal=ctx.decision.goal,
    )
    mode_states = {item.get("mode_state") for item in services.statuses}
    assert {"resolve_target", "delegating", "delegate_result", "done"}.issubset(
        mode_states
    )


def test_delegate_prepare_rejects_unknown_target_agent() -> None:
    ctx, _services, _logger = _ctx(registry_data={})

    preparation = DelegateMode().prepare(ctx)

    assert preparation.mode_result is not None
    assert preparation.mode_result.status == "error"
    assert "Unknown delegate target agent" in str(preparation.mode_result.message)


def test_delegate_validate_allows_fresh_pre_execution_state() -> None:
    ctx, _services, _logger = _ctx(
        registry_data={"agent.weather": {"state": "healthy"}}
    )

    validation = DelegateMode().validate(ctx)

    assert validation is not None
    assert validation.passed is True


def test_delegate_validate_accepts_output_only_delegate_result() -> None:
    ctx, _services, _logger = _ctx(
        registry_data={"agent.weather": {"state": "healthy"}}
    )
    ctx.state.last_result = ActionResult(
        command_id="cmd-delegate",
        status="success",
        summary="",
        outputs={"body": "delegate ok"},
    )

    validation = DelegateMode().validate(ctx)

    assert validation is not None
    assert validation.passed is True


@pytest.mark.parametrize(
    ("status", "error", "expected_message"),
    [
        (
            "failed",
            ActionError(code="A2A_FAILED", message="target exploded"),
            "target exploded",
        ),
        (
            "timeout",
            ActionError(code="TIMEOUT", message="took too long"),
            "Delegate completed cleanly.",
        ),
    ],
)
def test_delegate_handler_maps_failure_and_timeout_to_error(
    status: str,
    error: ActionError,
    expected_message: str,
) -> None:
    ctx, _services, _logger = _ctx(
        registry_data={"agent.weather": {"state": "healthy"}},
        action_result=ActionResult(
            command_id="cmd-delegate",
            status=status,
            summary="Delegate completed cleanly.",
            error=error,
        ),
    )

    result = DelegateMode().execute(ctx)

    assert result.status == "error"
    assert expected_message in str(result.message)


def test_delegate_prepare_rejects_when_a2a_budget_is_exhausted() -> None:
    ctx, _services, _logger = _ctx(
        registry_data={"agent.weather": {"state": "healthy"}}
    )
    ctx.state.budgets_remaining.a2a_calls = 0

    preparation = DelegateMode().prepare(ctx)

    assert preparation.mode_result is not None
    assert preparation.mode_result.status == "error"
    assert "a2a budget exhausted" in str(preparation.mode_result.message).lower()


def test_delegate_synthesize_flag_calls_result_synthesizer(monkeypatch) -> None:
    ctx, _services, _logger = _ctx(
        registry_data={"agent.weather": {"state": "healthy"}},
        synthesize_result=True,
    )
    mode = DelegateMode()
    called: dict[str, Any] = {}

    def _fake_synthesize(*, ctx, results):
        called["ctx"] = ctx
        called["results"] = results
        return PassThroughSynthesizer().synthesize(ctx=ctx, results=results)

    monkeypatch.setattr(mode._synthesizer, "synthesize", _fake_synthesize)

    result = mode.execute(ctx)

    assert result.status == "done"
    assert called["results"]
    assert called["results"][0].goal == "Check the weather in San Francisco"


def test_delegate_direct_dispatch_enforces_explicit_depth_limit_before_handler_runs() -> (
    None
):
    profile = _profile().model_copy(
        update={
            "mode_config": {
                BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED: ModeProfileConfig(
                    max_depth=1
                )
            }
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = LocalSessionStore(root / "sessions")
        runner = BrainRunner(
            profile=profile,
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            tool_api=LocalToolAdapter(),
            a2a_api=LocalA2AAdapter(),
            memory_api=LocalMemoryAdapter(root / "memory"),
            policy_api=LocalPolicyAdapter(),
            options=RunnerOptions(metactl_enabled=False),
        )
        decision = SimpleNamespace(
            mode=BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
            confidence=0.9,
            reason_code="delegate_deep",
            target_agent_id="agent.weather",
            goal="check weather",
            constraints="",
            synthesize_result=False,
            timeout_ms=None,
        )

        with pytest.raises(ValueError, match="depth exceeded"):
            invoke_decision_direct(
                runner,
                state=WorkingState(
                    session_id="s-depth",
                    agent_id="router-agent",
                    budgets_remaining=BudgetCounters(
                        ticks=4,
                        tool_calls=4,
                        a2a_calls=4,
                        tokens=4000,
                        time_ms=10000,
                    ),
                ),
                decision=decision,
                user_input="delegate weather",
                logger=MagicMock(),
                depth=2,
            )


def test_delegate_needs_user_fails_closed_in_v1() -> None:
    ctx, _services, _logger = _ctx(
        registry_data={"agent.weather": {"state": "healthy"}},
        action_result=ActionResult(
            command_id="cmd-delegate",
            status="needs_user",
            summary="Which city should I use?",
        ),
    )

    result = DelegateMode().execute(ctx)

    assert result.status == "error"
    assert "fails closed on clarification" in str(result.message)


def test_delegate_registration_is_internal_only_after_intent_first_cutover() -> None:
    available = available_routes()
    spec = get_route_descriptor("delegate")
    schema = DecisionAdapter.json_schema()

    assert available == ["act", "respond"]
    assert spec is None
    assert DelegateMode.mode_name == "execution_target_delegated"
    assert DelegateMode.has_prepare is True
    assert isinstance(DelegateMode.has_resume, property)
    assert DelegateMode().has_resume is False
    assert DelegateMode.has_validate is True
    assert DelegateMode.mode_category == "workflow"
    assert DelegateMode.default_config["max_depth"] == 1
    assert "target_agent_id" not in schema["properties"]
    assert "goal" not in schema["properties"]
    assert "synthesize_result" not in schema["properties"]


def test_mode_profile_config_round_trips_existing_fields_with_delegate_profile() -> (
    None
):
    config = ModeProfileConfig(
        enabled=True,
        delegate_async=True,
        max_depth=1,
        priority_hint=65,
        max_subtasks=3,
        max_decompose_depth=1,
    )
    dumped = config.model_dump(mode="python")
    loaded = ModeProfileConfig.model_validate(dumped)

    assert loaded.enabled is True
    assert loaded.delegate_async is True
    assert loaded.max_depth == 1
    assert loaded.priority_hint == 65
    assert loaded.max_subtasks == 3
    assert loaded.max_decompose_depth == 1


def test_delegate_prepare_cancels_before_dispatch_when_cancel_flag_is_set() -> None:
    ctx, services, _logger = _ctx(registry_data={"agent.weather": {"state": "healthy"}})
    ctx.options.decompose_cancel_requested = True

    result = DelegateMode().execute(ctx)

    assert result.status == "stopped"
    assert "cancelled before execution" in str(result.message).lower()
    assert services.command_calls == []
