from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openminion.modules.brain.execution.loop_contracts import ExecutionResult
from openminion.modules.brain.execution.targets.delegated.contracts import (
    A2AStatusMapper,
    AgentDiscoveryProvider,
    AgentResolver,
    AsyncCancellationPolicy,
    BudgetPolicy,
    CancellationPolicy,
    ClarificationAction,
    ClarificationPolicy,
    ContextInheritancePolicy,
    DelegatePayload,
    DelegationObserver,
    DelegationStrategy,
    FailurePolicy,
    IdempotencyKeyGenerator,
    ResultSynthesizer,
)
from openminion.modules.brain.execution.targets.delegated.strategies import (
    AbortOnNewMessagePolicy,
    AcceptOrFailResolver,
    DefaultAsyncCancellationPolicy,
    DirectStatusMapper,
    FailFastPolicy,
    FailOnClarificationPolicy,
    HashKeyGenerator,
    PassThroughSynthesizer,
    RegistryDiscoveryProvider,
    SimpleA2ABudgetPolicy,
    StatusMessageObserver,
    SummaryInheritancePolicy,
    SyncCommandStrategy,
)
from openminion.modules.brain.schemas import (
    ActionError,
    ActionResult,
    BudgetCounters,
    DelegationContext,
)


def _ctx():
    statuses: list[dict] = []
    commands: list[object] = []

    def _emit_status(**kwargs):
        statuses.append(dict(kwargs))

    def _act_command(*, command):
        commands.append(command)
        return (
            ActionResult(
                command_id=command.command_id,
                status="success",
                summary="delegated ok",
            ),
            None,
        )

    return (
        SimpleNamespace(
            state=SimpleNamespace(
                session_id="s-delegate",
                trace_id="t-delegate",
                budgets_remaining=BudgetCounters(
                    ticks=5,
                    tool_calls=5,
                    a2a_calls=1,
                    tokens=1000,
                    time_ms=10000,
                ),
            ),
            user_input="delegate this",
            emit_status=_emit_status,
            act_command=_act_command,
            _services=SimpleNamespace(runner=SimpleNamespace(agent_registry={})),
        ),
        statuses,
        commands,
    )


def test_delegate_contract_types_are_runtime_checkable() -> None:
    assert isinstance(AcceptOrFailResolver(), AgentResolver)
    assert isinstance(SyncCommandStrategy(), DelegationStrategy)
    assert isinstance(DirectStatusMapper(), A2AStatusMapper)
    assert isinstance(FailOnClarificationPolicy(), ClarificationPolicy)
    assert isinstance(RegistryDiscoveryProvider(), AgentDiscoveryProvider)
    assert isinstance(HashKeyGenerator(), IdempotencyKeyGenerator)
    assert isinstance(StatusMessageObserver(), DelegationObserver)
    assert isinstance(SimpleA2ABudgetPolicy(), BudgetPolicy)
    assert isinstance(PassThroughSynthesizer(), ResultSynthesizer)
    assert isinstance(FailFastPolicy(), FailurePolicy)
    assert isinstance(SummaryInheritancePolicy(), ContextInheritancePolicy)
    assert isinstance(AbortOnNewMessagePolicy(), CancellationPolicy)
    assert isinstance(DefaultAsyncCancellationPolicy(), AsyncCancellationPolicy)


def test_delegate_payload_requires_target_agent_id_and_goal() -> None:
    with pytest.raises(ValidationError):
        DelegatePayload(target_agent_id="", goal="delegate")
    with pytest.raises(ValidationError):
        DelegatePayload(target_agent_id="agent.weather", goal="")


def test_clarification_action_enum_values_are_stable() -> None:
    assert ClarificationAction.FAIL.value == "fail"
    assert ClarificationAction.BUBBLE_UP.value == "bubble_up"
    assert ClarificationAction.AUTO_ANSWER.value == "auto_answer"


def test_accept_or_fail_resolver_accepts_registered_agent_and_rejects_unknown() -> None:
    resolver = AcceptOrFailResolver()

    assert (
        resolver.resolve(
            target_agent_id="agent.weather",
            target_capability=None,
            registry={"agent.weather": {"state": "healthy"}},
        )
        == "agent.weather"
    )
    with pytest.raises(ValueError, match="Unknown delegate target agent"):
        resolver.resolve(
            target_agent_id="agent.unknown",
            target_capability=None,
            registry={"agent.weather": {"state": "healthy"}},
        )


def test_accept_or_fail_resolver_rejects_unavailable_agent() -> None:
    resolver = AcceptOrFailResolver()

    with pytest.raises(ValueError, match="unavailable"):
        resolver.resolve(
            target_agent_id="agent.weather",
            target_capability=None,
            registry={"agent.weather": {"state": "offline"}},
        )


def test_sync_command_strategy_builds_agent_command_and_calls_ctx_act_command() -> None:
    ctx, _statuses, commands = _ctx()
    strategy = SyncCommandStrategy()

    execution = strategy.execute(
        ctx=ctx,
        payload=DelegatePayload(
            target_agent_id="agent.weather",
            goal="check forecast",
            constraints="use sf",
            timeout_ms=2500,
            delegation_context=DelegationContext(
                summary="Parent inspected weather.py and needs forecast validation.",
                artifacts=["artifact://weather-report"],
                intent_id="intent-weather",
            ),
        ),
        resolved_agent_id="agent.weather",
        delegation_context=SummaryInheritancePolicy().build_child_context(
            parent_state=SimpleNamespace(
                goal="parent goal",
                last_result=None,
                constraints=["keep it short"],
                active_skill_id="skill-1",
            ),
            subtask=SimpleNamespace(goal="check forecast", constraints="use sf"),
        ),
        idempotency_key="delegate-key-1",
    )

    assert execution.action_result.status == "success"
    assert execution.job is None
    command = execution.command
    assert commands and commands[0] is command
    assert command.target_agent_id == "agent.weather"
    assert command.method == "delegate"
    assert command.params["goal"] == "check forecast"
    assert command.params["constraints"] == ["keep it short", "use sf"]
    assert command.params["active_skill_id"] == "skill-1"
    assert command.params["delegation_context"] == {
        "summary": "Parent inspected weather.py and needs forecast validation.",
        "artifacts": ["artifact://weather-report"],
        "intent_id": "intent-weather",
    }
    assert command.idempotency_key == "delegate-key-1"
    assert command.timeout_ms == 2500
    assert command.expect_async is False


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        ("success", None, "done"),
        ("failed", ActionError(code="FAILED", message="broken"), "error"),
        ("timeout", ActionError(code="TIMEOUT", message="late"), "error"),
        ("needs_user", None, "waiting_user"),
        ("blocked", ActionError(code="BUDGET_EXCEEDED", message="no budget"), "error"),
        ("retry", ActionError(code="RETRY", message="retry me"), "error"),
    ],
)
def test_direct_status_mapper_covers_all_action_statuses(
    status: str,
    error: ActionError | None,
    expected: str,
) -> None:
    ctx, _statuses, _commands = _ctx()
    mapper = DirectStatusMapper()

    result = mapper.map_result(
        ctx=ctx,
        payload=DelegatePayload(target_agent_id="agent.weather", goal="forecast"),
        resolved_agent_id="agent.weather",
        action_result=ActionResult(
            command_id="cmd-1",
            status=status,
            summary=f"status={status}",
            error=error,
        ),
    )

    assert result.status == expected


def test_fail_on_clarification_policy_returns_fail() -> None:
    policy = FailOnClarificationPolicy()
    ctx, _statuses, _commands = _ctx()

    assert (
        policy.on_clarification_needed(
            delegate_result=ActionResult(
                command_id="cmd-1",
                status="needs_user",
                summary="Need clarification.",
            ),
            original_context=ctx,
        )
        == ClarificationAction.FAIL
    )


def test_pass_through_synthesizer_returns_first_delegate_result() -> None:
    ctx, _statuses, _commands = _ctx()
    synthesizer = PassThroughSynthesizer()

    result = synthesizer.synthesize(
        ctx=ctx,
        results=[
            SimpleNamespace(
                status="completed",
                output="Delegated answer",
                error=None,
            )
        ],
    )

    assert isinstance(result, ExecutionResult)
    assert result.status == "done"
    assert result.message == "Delegated answer"


def test_hash_key_generator_is_deterministic() -> None:
    generator = HashKeyGenerator()

    first = generator.generate(
        session_id="s-1",
        trace_id="t-1",
        goal="summarize weather",
    )
    second = generator.generate(
        session_id="s-1",
        trace_id="t-1",
        goal="summarize weather",
    )
    third = generator.generate(
        session_id="s-1",
        trace_id="t-2",
        goal="summarize weather",
    )

    assert first == second
    assert first != third


def test_status_message_observer_emits_delegate_prefixed_status() -> None:
    ctx, statuses, _commands = _ctx()
    observer = StatusMessageObserver()

    observer.emit(
        ctx=ctx,
        mode_state="delegating",
        label='[delegate] calling agent:agent.weather with goal: "forecast"',
        target_agent_id="agent.weather",
    )

    assert statuses
    assert statuses[-1]["detail_text"].startswith("[delegate]")
    assert statuses[-1]["mode"] == "act"
    assert statuses[-1]["payload"]["execution.target"] == "delegated"


def test_simple_a2a_budget_policy_checks_remaining_budget() -> None:
    ctx, _statuses, _commands = _ctx()
    policy = SimpleA2ABudgetPolicy()

    assert policy.check_budget(state=ctx.state) is True
    ctx.state.budgets_remaining.a2a_calls = 0
    assert policy.check_budget(state=ctx.state) is False
