from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from openminion.modules.llm.providers.base import (
    ProviderResponse,
    ProviderToolSpec,
    ProviderToolCall,
)
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.execution.dependencies import ExecutorDeps
from openminion.services.agent.execution.response import tool_calls_payload
from openminion.services.agent.execution.required.completion import (
    _call_initial_final_response,
    _retry_stale_draft_final_response,
    post_execution_follow_up_result,
)
from openminion.services.agent.execution.required.state import CompletionContext


def _tool_batch_metadata(
    *, batch: ToolExecutionBatch, tool_calls_count: int
) -> dict[str, str]:
    payload = [
        {
            "tool_name": item.tool_name,
            "ok": item.ok,
            "verified": item.verified,
            "content": item.content,
            "error": item.error,
            "data": dict(item.data),
            "call_id": item.call_id,
            "source": item.source,
        }
        for item in list(batch.results or [])
    ]
    return {
        "tool_results": json.dumps(payload, sort_keys=True),
        "tool_calls_count": str(tool_calls_count),
        "tool_execution_count": str(len(payload)),
        "tool_verified": str(all(bool(item["verified"]) for item in payload)).lower(),
    }


def _deps() -> ExecutorDeps:
    return ExecutorDeps(
        finalize_response=lambda response: response,
        identity_metadata=lambda: {},
        tool_batch_metadata=_tool_batch_metadata,
    )


@dataclass
class _FakeRuntimeOps:
    provider_responses: list[ProviderResponse]
    provider_index: int = 0
    provider_requests: list[Any] = field(default_factory=list)

    async def call_provider(self, request, *, tool_call_strategy: str):
        del tool_call_strategy
        self.provider_requests.append(request)
        response = self.provider_responses[self.provider_index]
        self.provider_index += 1
        return response


def _tool_specs() -> list[ProviderToolSpec]:
    return [
        ProviderToolSpec(
            name="web.fetch",
            description="fetch a URL",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        ),
        ProviderToolSpec(
            name="file.write",
            description="write a file",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        ),
    ]


def _runner(runtime_ops: _FakeRuntimeOps) -> Any:
    return SimpleNamespace(
        runtime_ops=runtime_ops,
        runtime=SimpleNamespace(
            inbound=SimpleNamespace(channel="console", target="cli"),
            system_prompt="system",
            provider_history=[],
        ),
        service_port=SimpleNamespace(
            provider=SimpleNamespace(name="openai"),
            tools=SimpleNamespace(
                model_provider_specs=lambda: list(_tool_specs()),
            ),
        ),
    )


def test_required_lane_initial_follow_up_recovers_minimax_tool_json_batch() -> None:
    runtime_ops = _FakeRuntimeOps(
        provider_responses=[
            ProviderResponse(
                text=(
                    '{"tool":"web.fetch","tool_call_id":"fetch1",'
                    '"url":"https://packaging.python.org/en/latest/guides/writing-pyproject-toml/"}\n'
                    '{"tool":"file.write","tool_call_id":"write1",'
                    '"path":"/tmp/project/pyproject.toml","content":"[project]\\nname=\\"demo\\"\\n"}'
                ),
                model="MiniMax-M2.7",
                finish_reason="stop",
            )
        ]
    )
    response = ProviderResponse(
        text="Draft plan before tools.",
        model="MiniMax-M2.7",
        finish_reason="tool_calls",
    )

    final_response = asyncio.run(
        _call_initial_final_response(
            _runner(runtime_ops),
            response=response,
            tool_feedback_message="Tool execution results:\n[]",
            tool_call_strategy="auto",
        )
    )

    assert runtime_ops.provider_requests
    assert runtime_ops.provider_requests[0].tools
    assert len(final_response.tool_calls) == 2
    assert final_response.tool_calls[0].name == "web.fetch"
    assert final_response.tool_calls[1].name == "file.write"


def test_required_lane_retries_when_follow_up_repeats_pre_tool_draft() -> None:
    runtime_ops = _FakeRuntimeOps(
        provider_responses=[
            ProviderResponse(
                text=(
                    "**PLAN**\n\n"
                    "uv installs a Rust-based Python toolchain and can manage projects, "
                    "while pipx installs Python apps into isolated virtual environments."
                ),
                model="MiniMax-M2.7",
                finish_reason="stop",
            )
        ]
    )
    response = ProviderResponse(
        text="**PLAN**\n1. Gather evidence\n2. Compare tools\n3. Summarize findings",
        model="MiniMax-M2.7",
        finish_reason="tool_calls",
    )

    final_response = asyncio.run(
        _retry_stale_draft_final_response(
            _runner(runtime_ops),
            response=response,
            final_response=response,
            tool_feedback_payload="[]",
            tool_feedback_message="Tool execution results:\n[]",
            requires_finalization_status=False,
            tool_call_strategy="auto",
        )
    )

    assert runtime_ops.provider_requests
    assert (
        "Do not repeat the pre-tool draft"
        in runtime_ops.provider_requests[0].user_message
    )
    assert "while pipx installs Python apps" in final_response.text


def test_required_lane_replans_once_on_duplicate_final_tool_call() -> None:
    runtime_ops = _FakeRuntimeOps(
        provider_responses=[
            ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name="web.fetch",
                        arguments={"url": "https://example.com"},
                        source="native",
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResponse(
                text="Final answer from existing fetch result.",
                model="fake-model",
                finish_reason="stop",
            ),
        ]
    )
    response = ProviderResponse(
        text="",
        model="fake-model",
        tool_calls=[
            ProviderToolCall(
                name="web.fetch",
                arguments={"url": "https://example.com"},
                source="native",
            )
        ],
        finish_reason="tool_calls",
    )
    batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="web.fetch",
                ok=True,
                content="example content",
                verified=True,
            )
        ]
    )

    result = asyncio.run(
        post_execution_follow_up_result(
            _runner(runtime_ops),
            deps=_deps(),
            context=CompletionContext(
                response=response,
                batch=batch,
                intent_category="research",
                tool_call_strategy="auto",
                tool_budget_state=None,
                attempted_tools=["web.fetch"],
                capability_fallback_trigger_reason=None,
                tool_calls_sig=tool_calls_payload(response.tool_calls),
                shared_capability_meta={},
            ),
        )
    )

    assert result.action == "return"
    assert result.outcome is not None
    assert result.outcome.response.text == "Final answer from existing fetch result."
    assert len(runtime_ops.provider_requests) == 2
    assert runtime_ops.provider_requests[1].metadata["duplicate_tool_replan"] == "true"
