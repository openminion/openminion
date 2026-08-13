from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openminion.modules.llm.contracts import (
    ProviderResponse,
    ProviderToolSpec,
)
from openminion.modules.tool import ToolExecutionBatch
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.tool.contracts import ProviderToolCall
from openminion.services.agent.execution.unforced.followup import (
    build_follow_up_request,
)
from openminion.services.agent.execution.followup import (
    available_follow_up_tools,
    recover_text_tool_calls,
)


def _runner(*, tools: Any | None, provider_name: str = "minimax") -> Any:
    return SimpleNamespace(
        service_port=SimpleNamespace(
            provider=SimpleNamespace(name=provider_name),
            tools=tools,
        )
    )


def test_available_follow_up_tools_falls_back_to_provider_specs() -> None:
    spec = ProviderToolSpec(
        name="file.read",
        description="Read a file",
        parameters={"type": "object"},
    )

    def _raise_model_specs() -> list[ProviderToolSpec]:
        raise RuntimeError("model provider specs unavailable")

    tools = SimpleNamespace(
        model_provider_specs=_raise_model_specs,
        provider_specs=lambda: [spec, object()],
    )

    assert available_follow_up_tools(_runner(tools=tools)) == [spec]


def test_recover_text_tool_calls_parses_follow_up_json_without_internal_reach() -> None:
    spec = ProviderToolSpec(
        name="file.read",
        description="Read a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    response = ProviderResponse(
        text='{"tool":"file.read","path":"/tmp/demo.txt"}',
        model="MiniMax-M2.7",
    )
    tools = SimpleNamespace(model_provider_specs=lambda: [spec])

    recovered = recover_text_tool_calls(_runner(tools=tools), response=response)

    assert recovered.finish_reason == "tool_calls"
    assert recovered.text == ""
    assert len(recovered.tool_calls) == 1
    assert recovered.tool_calls[0].name == "file.read"
    assert recovered.tool_calls[0].arguments == {"path": "/tmp/demo.txt"}
    assert recovered.normalization["fallback_parse_mode"] == "json_payload"


def test_follow_up_history_preserves_calls_and_linked_tool_results() -> None:
    call = ProviderToolCall(
        id="call-1",
        name="file.read",
        arguments={"path": "/tmp/demo.txt"},
    )
    response = ProviderResponse(text="", model="model", tool_calls=[call])
    batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="file.read",
                ok=True,
                content="hello",
                data={"content": "hello"},
                call_id="call-1",
            )
        ]
    )
    runner = SimpleNamespace(
        runtime=SimpleNamespace(system_prompt="system", provider_history=[]),
        service_port=SimpleNamespace(
            provider=SimpleNamespace(name="adapter-neutral"),
            tools=SimpleNamespace(model_provider_specs=lambda: []),
        ),
    )

    request = build_follow_up_request(
        runner,
        deps=SimpleNamespace(),
        response=response,
        batch=batch,
    )

    assert request.history[0].role == "assistant"
    assert request.history[0].tool_calls == [call]
    assert request.history[1].role == "tool"
    assert request.history[1].tool_call_id == "call-1"
    assert request.history[1].tool_status == "success"
    assert not any(item.role == "user" for item in request.history)
