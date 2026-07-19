from __future__ import annotations

from types import SimpleNamespace
import unittest.mock

from openminion.base.types import AgentResponse, Message
from openminion.modules.llm.providers.base import ProviderResponse
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.execution.dependencies import ExecutorDeps
from openminion.services.agent.execution.unforced import UnforcedLaneRunner


class _FakeRuntimeOps:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.provider_calls = 0

    async def execute_tool_calls(
        self,
        tool_calls,
        *,
        tool_budget_state=None,
        context_metadata_overrides=None,
    ):
        del tool_calls, tool_budget_state, context_metadata_overrides
        self.execute_calls += 1
        batch = ToolExecutionBatch(
            results=[
                ToolExecutionResult(
                    tool_name="weather.openmeteo.current",
                    ok=True,
                    verified=True,
                    content="Tokyo weather now",
                    data={"city": "Tokyo"},
                )
            ]
        )
        return batch, [], False

    def record_self_improvement(self, *, user_message, tool_results):
        del user_message, tool_results

    def _collect_batch_output(self, batch: ToolExecutionBatch) -> str:
        return batch.results[0].content

    async def call_provider(self, request, *, tool_call_strategy):
        del request, tool_call_strategy
        self.provider_calls += 1
        return ProviderResponse(
            text="Final answer after tool execution",
            model="fake-model",
            finish_reason="stop",
            tool_calls=[],
        )


class _FakeMaxStepsRuntimeOps:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.provider_calls = 0

    async def execute_tool_calls(
        self,
        tool_calls,
        *,
        tool_budget_state=None,
        context_metadata_overrides=None,
    ):
        del tool_calls, tool_budget_state, context_metadata_overrides
        self.execute_calls += 1
        return (
            ToolExecutionBatch(
                results=[
                    ToolExecutionResult(
                        tool_name="weather.openmeteo.current",
                        ok=True,
                        verified=True,
                        content="Tokyo weather now",
                        data={"city": "Tokyo"},
                    )
                ]
            ),
            [],
            False,
        )

    def record_self_improvement(self, *, user_message, tool_results):
        del user_message, tool_results

    def _collect_batch_output(self, batch: ToolExecutionBatch) -> str:
        return batch.results[0].content

    async def call_provider(self, request, *, tool_call_strategy):
        del request, tool_call_strategy
        self.provider_calls += 1
        return ProviderResponse(
            text="Need another tool",
            model="fake-model",
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "name": f"weather.openmeteo.current.{self.provider_calls}",
                    "arguments": {"city": "Tokyo"},
                }
            ],
        )


def _deps() -> ExecutorDeps:
    return ExecutorDeps(
        finalize_response=lambda response: response,
        identity_metadata=lambda: {},
        tool_batch_metadata=lambda *, batch, tool_calls_count: {
            "tool_calls_count": str(tool_calls_count),
            "tool_execution_count": str(len(batch.results)),
            "tool_results": batch.to_metadata_payload(),
        },
    )


def _runner(runtime_ops, *, max_steps: int) -> UnforcedLaneRunner:
    runtime = SimpleNamespace(
        inbound=Message(channel="console", target="me", body="weather"),
        user_message="weather",
        system_prompt="system",
        provider_history=[],
    )
    service_port = SimpleNamespace(
        config=SimpleNamespace(runtime=SimpleNamespace(agent_loop_max_steps=max_steps)),
        tools=None,
        provider=SimpleNamespace(name=""),
    )
    return UnforcedLaneRunner(
        service_port=service_port,
        runtime=runtime,
        runtime_ops=runtime_ops,
    )


async def _run_handle_unforced_tool_calls() -> AgentResponse:
    runtime_ops = _FakeRuntimeOps()
    flow = _runner(runtime_ops, max_steps=4)
    initial_response = ProviderResponse(
        text="Tool requested",
        model="fake-model",
        finish_reason="tool_calls",
        tool_calls=[{"name": "weather", "arguments": {"city": "Tokyo"}}],
    )
    with unittest.mock.patch(
        "openminion.modules.brain.loop.tools.engine.run_adaptive_tool_loop",
        side_effect=AssertionError(
            "legacy turn-flow compatibility loop should not route through the shared brain engine"
        ),
    ) as shared_engine:
        response = await flow.handle_unforced_tool_calls(
            initial_response=initial_response,
            intent_category="weather",
            tool_call_strategy="native",
            tool_budget_state=None,
            deps=_deps(),
        )
    shared_engine.assert_not_called()
    assert runtime_ops.execute_calls == 1
    assert runtime_ops.provider_calls == 1
    return response


def test_legacy_agent_execution_tool_loop_remains_independent_from_shared_brain_engine() -> (
    None
):
    import asyncio

    response = asyncio.run(_run_handle_unforced_tool_calls())

    assert response.text == "Final answer after tool execution"
    assert response.metadata["tool_loop_termination_reason"] == "model_final"
    assert response.metadata["tool_execution_count"] == "1"
    assert "weather.openmeteo.current" in response.metadata["tool_results"]


def test_legacy_agent_execution_tool_loop_preserves_max_step_termination() -> None:
    import asyncio

    deps = ExecutorDeps(
        finalize_response=lambda response: response,
        identity_metadata=lambda: {},
        tool_batch_metadata=lambda *, batch, tool_calls_count: {
            "tool_calls_count": str(tool_calls_count),
            "tool_execution_count": str(len(batch.results)),
            "tool_results": batch.to_metadata_payload(),
        },
    )

    async def _run() -> AgentResponse:
        flow = _runner(_FakeMaxStepsRuntimeOps(), max_steps=2)
        return await flow.handle_unforced_tool_calls(
            initial_response=ProviderResponse(
                text="Tool requested",
                model="fake-model",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "name": "weather.openmeteo.current",
                        "arguments": {"city": "Tokyo"},
                    }
                ],
            ),
            intent_category="weather",
            tool_call_strategy="native",
            tool_budget_state=None,
            deps=deps,
        )

    response = asyncio.run(_run())

    assert response.text == "Tool loop reached max steps."
    assert response.metadata["tool_loop_termination_reason"] == "tool_loop_max_steps"
    assert response.metadata["tool_execution_count"] == "1"
