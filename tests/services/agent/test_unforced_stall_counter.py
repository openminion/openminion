"""The stall rule counts consecutive failures; progress clears the counter."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from openminion.base.types import Message
from openminion.modules.llm.providers.base import ProviderResponse, ProviderToolCall
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.constants import TERMINATION_REASON_LOOP_NO_PROGRESS
from openminion.services.agent.execution.unforced.loop import handle_unforced_tool_calls
from tests.services.agent.test_unforced_lane_recovery import (
    _FakeRuntimeOps,
    _deps,
    _final_answer_response,
)


def _runner(runtime_ops):
    return SimpleNamespace(
        runtime_ops=runtime_ops,
        runtime=SimpleNamespace(
            inbound=Message(channel="console", target="cli", body="do work"),
            system_prompt="system",
            provider_history=[],
            user_message="do work",
        ),
        service_port=SimpleNamespace(
            config=SimpleNamespace(runtime=SimpleNamespace(agent_loop_max_steps=8))
        ),
    )


def _call_response(step: int) -> ProviderResponse:
    return ProviderResponse(
        text="",
        model="fake-model",
        tool_calls=[
            ProviderToolCall(
                name="file.write",
                arguments={"path": f"/tmp/report-{step}.md", "content": "data"},
            )
        ],
        finish_reason="tool_calls",
    )


def _failure() -> ToolExecutionBatch:
    return ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="file.write",
                ok=False,
                verified=False,
                content="",
                error="rate limited",
                data={"error_code": "RATE_LIMITED"},
                call_id="call-1",
            )
        ]
    )


def _success() -> ToolExecutionBatch:
    return ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="file.write",
                ok=True,
                verified=True,
                content="wrote the report",
                data={"path": "/tmp/report.md"},
                call_id="call-1",
            )
        ]
    )


def _run(batches: list[ToolExecutionBatch]):
    runtime_ops = _FakeRuntimeOps(
        execute_batches=[(batch, [], False) for batch in batches],
        provider_responses=[_call_response(index) for index in range(1, len(batches))]
        + [_final_answer_response("Finished the report.")],
    )
    result = asyncio.run(
        handle_unforced_tool_calls(
            _runner(runtime_ops),
            initial_response=_call_response(0),
            intent_category="coding",
            tool_call_strategy="auto",
            tool_budget_state=None,
            deps=_deps(),
        )
    )
    return runtime_ops, result


def test_progress_between_failures_does_not_trip_the_stall_rule() -> None:
    runtime_ops, result = _run([_failure(), _success(), _failure(), _success()])

    assert runtime_ops.execute_index == 4
    assert result.metadata["tool_loop_termination_reason"] == "model_final"
    assert result.metadata["finish_reason"] == "stop"
    assert result.text == "Finished the report."


def test_two_consecutive_failures_still_trip_the_stall_rule() -> None:
    runtime_ops, result = _run([_failure(), _failure()])

    assert runtime_ops.execute_index == 2
    assert result.metadata["tool_loop_termination_reason"] == (
        TERMINATION_REASON_LOOP_NO_PROGRESS
    )
