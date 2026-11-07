"""A no-tool-call response must return the model's answer, not a step-cap error."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.base.types import Message
from openminion.modules.llm.providers.base import ProviderResponse, ProviderToolCall
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.execution.response import tool_calls_payload
from openminion.services.agent.execution.unforced.loop import handle_unforced_tool_calls
from openminion.services.agent.execution.unforced.metadata import direct_tool_response
from tests.services.agent.test_unforced_lane_recovery import (
    _FakeRuntimeOps,
    _deps,
    _final_answer_response,
)


def _runner(runtime_ops, *, max_steps: int = 5):
    return SimpleNamespace(
        runtime_ops=runtime_ops,
        runtime=SimpleNamespace(
            inbound=Message(channel="console", target="cli", body="do work"),
            system_prompt="system",
            provider_history=[],
            user_message="do work",
        ),
        service_port=SimpleNamespace(
            config=SimpleNamespace(
                runtime=SimpleNamespace(agent_loop_max_steps=max_steps)
            )
        ),
    )


def _run(runtime_ops, *, max_steps: int = 5):
    return asyncio.run(
        handle_unforced_tool_calls(
            _runner(runtime_ops, max_steps=max_steps),
            initial_response=runtime_ops.provider_responses[0],
            intent_category="coding",
            tool_call_strategy="auto",
            tool_budget_state=None,
            deps=_deps(),
        )
    )


def _tool_call_response(step: int) -> ProviderResponse:
    return ProviderResponse(
        text="",
        model="fake-model",
        tool_calls=[
            ProviderToolCall(
                id=f"call-{step}",
                name="file.read",
                arguments={"path": f"f{step}.txt"},
            )
        ],
    )


def _batch() -> ToolExecutionBatch:
    return ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="file.read",
                ok=True,
                verified=True,
                content="body",
                error="",
                data={},
                source="native",
            )
        ]
    )


@pytest.mark.parametrize("provider_finish_reason", ["tool_calls", "stop"])
def test_direct_tool_path_keeps_its_finish_reason(
    provider_finish_reason: str,
) -> None:
    """The direct-tool terminal publishes `tool_direct` whatever the provider said."""
    response = ProviderResponse(
        text="",
        model="fake-model",
        tool_calls=[
            ProviderToolCall(id="call-1", name="file.read", arguments={"path": "f.txt"})
        ],
        finish_reason=provider_finish_reason,
    )

    result = direct_tool_response(
        _runner(_FakeRuntimeOps(execute_batches=[], provider_responses=[])),
        deps=_deps(),
        response=response,
        batch=_batch(),
        intent_category="coding",
        signature=tool_calls_payload(response.tool_calls),
    )

    assert result.text == "body"
    assert result.metadata["finish_reason"] == "tool_direct"
    assert result.metadata["tool_loop_termination_reason"] == "tool_direct"
    assert result.metadata["tool_calls_count"] == "1"
    assert result.metadata["tool_execution_count"] == "1"


def test_answer_after_tool_steps_ends_the_turn() -> None:
    """Caller-shaped route: initial tool call, follow-up tool call, then the answer."""
    runtime_ops = _FakeRuntimeOps(
        execute_batches=[(_batch(), [], False), (_batch(), [], False)],
        provider_responses=[
            _tool_call_response(step=1),
            _final_answer_response("Here is the answer."),
        ],
    )

    result = asyncio.run(
        handle_unforced_tool_calls(
            _runner(runtime_ops, max_steps=6),
            initial_response=_tool_call_response(step=0),
            intent_category="coding",
            tool_call_strategy="auto",
            tool_budget_state=None,
            deps=_deps(),
        )
    )

    assert result.text == "Here is the answer."
    assert result.metadata["finish_reason"] == "stop"
    assert result.metadata["tool_loop_termination_reason"] == "model_final"
    status = json.loads(result.metadata[STATE_KEY_FINALIZATION_STATUS])
    assert status["status"] == "final_answer"
    assert result.metadata["tool_calls_count"] == "1"
    assert result.metadata["tool_execution_count"] == "1"
    assert runtime_ops.execute_index == 2
    assert runtime_ops.provider_index == 2


def test_no_tool_call_response_returns_the_answer() -> None:
    """`handle_unforced_tool_calls` contract: a response with no tool calls is the answer.

    The turn caller (`flow._handle_unforced_provider_response`) returns early for a
    no-tool-call provider response, so a live turn does not reach this branch today;
    this pins the helper's own contract instead of the step-cap fallthrough.
    """
    runtime_ops = _FakeRuntimeOps(
        execute_batches=[],
        provider_responses=[
            ProviderResponse(text="all done", model="fake-model", finish_reason="stop")
        ],
    )

    result = _run(runtime_ops, max_steps=1)

    assert result.text == "all done"
    assert result.metadata["finish_reason"] == "stop"
    assert result.metadata["tool_loop_termination_reason"] == "model_final"
    assert runtime_ops.provider_index == 0
    assert runtime_ops.execute_index == 0


def test_iteration_cap_still_terminates_a_loop_that_keeps_calling_tools() -> None:
    """A loop that exhausts its steps still reports the cap, not a model answer."""
    runtime_ops = _FakeRuntimeOps(
        execute_batches=[(_batch(), [], False)],
        provider_responses=[_tool_call_response(step=1)],
    )

    result = _run(runtime_ops, max_steps=1)

    assert result.text == "Tool loop reached max steps."
    assert result.metadata["tool_loop_termination_reason"] == "tool_loop_max_steps"
    assert runtime_ops.execute_index == 1
    assert runtime_ops.provider_index == 1
