"""Regression checks for schema composition and final request budget guards."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from openminion.modules.brain.loop.context.task_context import (
    TaskContextBudgetError,
    message_token_estimate,
    with_task_update_schema,
)
from openminion.modules.brain.loop.tools.runtime import (
    DefaultAdaptiveToolLoopLLMRuntime,
)
from openminion.modules.brain.schemas.task_context import ResolvedTaskContext
from openminion.modules.llm.schemas import LLMResponse, Message, ToolSpec


def task():
    return ResolvedTaskContext(
        revision=1, task_id="guard-test", objective="Export", consumed_input="0" * 64
    )


@pytest.mark.parametrize("occupied", [False, True])
def test_task_definitions_preserve_runtime_refs_and_validate_both_payloads(occupied):
    definitions = {"TaskCondition": {"type": "string"}}
    if occupied:
        definitions["task_update_TaskCondition"] = {"type": "integer"}
    original = {
        "type": "object",
        "$defs": definitions,
        "properties": {"payload": {"$ref": "#/$defs/TaskCondition"}},
        "required": ["payload"],
    }
    spec = ToolSpec(name="runtime", input_schema=original)
    before = deepcopy(spec.input_schema)
    schema = with_task_update_schema(spec, required=True).input_schema
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    value = {
        "payload": "valid-runtime-input",
        "task_update": {
            "operation": "update",
            "expected_revision": 1,
            "user_quote": "Use USD",
            "set_conditions": [{"key": "currency", "quote": "USD"}],
        },
    }
    validator.validate(value)
    assert not validator.is_valid({**value, "payload": {"key": "x", "quote": "x"}})
    bad = deepcopy(value)
    bad["task_update"]["set_conditions"][0]["quote"] = ""
    assert not validator.is_valid(bad)
    assert schema["$defs"]["TaskCondition"] == before["$defs"]["TaskCondition"]
    if occupied:
        assert schema["$defs"]["task_update_TaskCondition"] == {"type": "integer"}
    assert spec.input_schema == before


@pytest.mark.parametrize(
    "estimator",
    [None, lambda **kwargs: 1, lambda **kwargs: (_ for _ in ()).throw(RuntimeError())],
)
def test_tool_schema_and_structured_tool_arguments_contribute_to_fallback(estimator):
    runner = SimpleNamespace(llm_api=SimpleNamespace(estimate_tokens=estimator))
    messages = [Message(role="user", content="Export")]
    large = ToolSpec(
        name="write",
        description="x" * 10000,
        input_schema={"type": "object", "description": "y" * 10000},
    )
    base = message_token_estimate(runner, messages)
    assert message_token_estimate(runner, messages, tools=[large]) > base + 4000
    from openminion.modules.llm.schemas import ToolCall

    structured = Message(
        role="assistant",
        tool_calls=[ToolCall(id="c", name="write", arguments={"body": "z" * 10000})],
    )
    assert message_token_estimate(runner, [structured]) > base + 2000


class CallClient:
    def __init__(self):
        self.requests = []

    def call(self, request):
        self.requests.append(request)
        return LLMResponse(ok=True, provider="test", model="test", output_text="done")


class CompleteClient:
    def __init__(self):
        self.requests = []

    def complete(self, messages, tools, **kwargs):
        self.requests.append(SimpleNamespace(messages=messages, tools=tools, **kwargs))
        return LLMResponse(ok=True, provider="test", model="test", output_text="done")


@pytest.mark.parametrize("client_type", [CallClient, CompleteClient])
@pytest.mark.parametrize("has_task", [False, True])
def test_runtime_blocks_large_actual_schema_before_call_and_preserves_task(
    client_type, has_task
):
    current = task() if has_task else None
    state = SimpleNamespace(
        resolved_task_context=current, budgets_remaining=SimpleNamespace(tokens=1000)
    )
    before = current.model_dump() if current else None
    client = client_type()
    runtime = DefaultAdaptiveToolLoopLLMRuntime.from_adapter(client, task_state=state)
    with pytest.raises(TaskContextBudgetError, match="tool schemas"):
        runtime.complete(
            messages=[Message(role="user", content="Export")],
            tools=[ToolSpec(name="large", description="x" * 10000)],
            model="test",
        )
    assert client.requests == []
    assert (state.resolved_task_context.model_dump() if current else None) == before
    assert state.budgets_remaining.tokens == 1000


@pytest.mark.parametrize("client_type", [CallClient, CompleteClient])
@pytest.mark.parametrize("has_task", [False, True])
@pytest.mark.parametrize("budget", [1000, 50000])
@pytest.mark.parametrize("requested", [None, 5000, 20])
def test_runtime_reserves_output_and_rechecks_current_budget_on_next_call(
    client_type, has_task, budget, requested
):
    state = SimpleNamespace(
        resolved_task_context=task() if has_task else None,
        budgets_remaining=SimpleNamespace(tokens=budget),
    )
    client = client_type()
    runtime = DefaultAdaptiveToolLoopLLMRuntime.from_adapter(client, task_state=state)
    messages = [Message(role="user", content="Export")]
    estimate = message_token_estimate(SimpleNamespace(), messages)
    runtime.complete(
        messages=messages, tools=[], model="test", max_output_tokens=requested
    )
    expected = min(requested, budget - estimate) if requested is not None else None
    assert getattr(client.requests[-1], "max_output_tokens", None) == expected
    state.budgets_remaining.tokens = 1
    with pytest.raises(TaskContextBudgetError):
        runtime.complete(messages=messages, tools=[], model="test")
    assert len(client.requests) == 1


def test_runtime_without_task_keeps_legacy_request_options():
    client = CallClient()
    runtime = DefaultAdaptiveToolLoopLLMRuntime.from_adapter(client)
    runtime.complete(messages=[], tools=[], model="test")
    assert client.requests[-1].max_output_tokens is None


def test_entry_large_schema_returns_waiting_user_without_provider_call(
    tmp_path, monkeypatch
):
    from tests.brain.task_context_test_support import Harness
    import openminion.modules.brain.loop.orchestration as entry

    h = Harness(tmp_path)
    try:
        h.initialize("guard")
        state = h.runner._load_or_init_state("guard")
        state.resolved_task_context = task()
        state.budgets_remaining.tokens = 10000
        h.runner._save_state(state)
        monkeypatch.setattr(
            entry,
            "build_entry_tool_specs",
            lambda *a, **k: (
                [
                    ToolSpec(
                        name="clarify",
                        description="x" * 100000,
                        input_schema={"type": "object"},
                    )
                ],
                False,
            ),
        )
        result = h.runner.step(session_id="guard", user_input="Use USD")
        assert result.status == "waiting_user"
        assert "budget" in result.message
        assert not h.provider.requests
        assert (
            result.working_state.resolved_task_context.model_dump()
            == task().model_dump()
        )
    finally:
        h.close()


def test_loop_budget_failure_is_waiting_user_without_consuming_provider_usage():
    from tests.brain.tool_loops.test_engine import _LoopContext, _state, _profile
    from openminion.modules.brain.loop.tools import run_adaptive_tool_loop

    state = _state(tokens=1000)
    state.resolved_task_context = task()
    ctx = _LoopContext(state=state)
    client = CallClient()
    runtime = DefaultAdaptiveToolLoopLLMRuntime.from_adapter(client, task_state=state)
    outcome = run_adaptive_tool_loop(
        ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="test",
        initial_messages=[Message(role="user", content="Export")],
        tool_specs=[ToolSpec(name="file.read", description="x" * 10000)],
    )
    assert outcome.mode_result.status == "waiting_user"
    assert not client.requests and not ctx.commands
    assert state.budgets_remaining.tokens == 1000
    assert state.llm_calls_used == 0


def test_entry_reconsideration_accounts_first_response_before_retry(tmp_path):
    from tests.brain.task_context_test_support import Harness
    from tests.brain.test_resolved_task_context import delta
    from openminion.modules.llm.schemas import UsageInfo

    h = Harness(tmp_path)
    try:
        h.initialize("retry-budget")
        state = h.runner._load_or_init_state("retry-budget")
        state.resolved_task_context = task()
        state.budgets_remaining.tokens = 20000
        h.runner._save_state(state)
        h.plan("retry-budget", ["Filename?"])
        response = h.provider.responses["retry-budget"][0]
        response.tool_calls[0].arguments["task_update"] = delta(1, "Use USD")
        response.usage = UsageInfo(
            input_tokens=8900, output_tokens=100, total_tokens=9000
        )
        h.provider.estimate_tokens = lambda **kwargs: (
            15000 if kwargs["context"].get("tools") else 1
        )
        h.provider.entry_session = "retry-budget"
        result = h.runner.step(session_id="retry-budget", user_input="Use USD")
        assert result.status == "waiting_user" and "budget" in result.message
        assert len(h.provider.requests) == 1
        assert result.working_state.llm_calls_used == 1
        assert result.working_state.budgets_remaining.tokens == 11000
        assert result.working_state.resolved_task_context.revision == 1
    finally:
        h.close()
