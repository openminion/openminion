from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from openminion.modules.llm.client_call import provider_history_payload
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
        runtime=SimpleNamespace(system_prompt="system", provider_history=[]),
        service_port=SimpleNamespace(
            provider=SimpleNamespace(name=provider_name),
            tools=tools,
        ),
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


def test_follow_up_history_preserves_structured_tool_error_facts() -> None:
    call = ProviderToolCall(id="call-1", name="file.list_dir", arguments={"path": "."})
    response = ProviderResponse(text="", model="model", tool_calls=[call])
    batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="file.list_dir",
                ok=False,
                content="",
                error="tool_budget_calls_exceeded",
                data={
                    "error_code": "tool_budget_calls_exceeded",
                    "error_details": {
                        "tool_name": "file.list_dir",
                        "tool_calls": 8,
                        "max_calls_per_tool": 8,
                    },
                },
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

    tool_message = request.history[1]
    assert tool_message.tool_error == {
        "code": "tool_budget_calls_exceeded",
        "message": "tool_budget_calls_exceeded",
        "details": {
            "tool_name": "file.list_dir",
            "tool_calls": 8,
            "max_calls_per_tool": 8,
        },
    }
    assert json.loads(tool_message.content)["error"] == tool_message.tool_error
    assert json.loads(tool_message.content) == {
        "status": "error",
        "output": None,
        "error": {
            "code": "tool_budget_calls_exceeded",
            "message": "tool_budget_calls_exceeded",
            "details": {
                "tool_name": "file.list_dir",
                "tool_calls": 8,
                "max_calls_per_tool": 8,
            },
        },
    }


def _follow_up_tool_message(result: ToolExecutionResult) -> Any:
    call = ProviderToolCall(
        id="call-1", name="file.read", arguments={"path": "/tmp/demo.txt"}
    )
    response = ProviderResponse(text="", model="model", tool_calls=[call])
    request = build_follow_up_request(
        _runner(tools=SimpleNamespace(model_provider_specs=lambda: [])),
        deps=SimpleNamespace(),
        response=response,
        batch=ToolExecutionBatch(results=[result]),
    )
    return request.history[1]


def test_a_truncated_success_carries_exact_length_facts() -> None:
    message = _follow_up_tool_message(
        ToolExecutionResult(
            tool_name="file.read",
            ok=True,
            content="note body",
            data={"truncated": True, "returned_length": 9, "total_length": 400000},
            call_id="call-1",
        )
    )

    expected = (
        '{"status": "success", "output": "note body", "error": null, '
        '"truncated": true, "returned_length": 9, "total_length": 400000}'
    )
    assert message.content == expected
    assert message.tool_call_id == "call-1"
    assert message.tool_status == "success"
    assert message.tool_error is None
    assert provider_history_payload(message) == {
        "role": "tool",
        "content": expected,
        "tool_call_id": "call-1",
        "tool_status": "success",
        "tool_output": {
            "truncated": True,
            "returned_length": 9,
            "total_length": 400000,
        },
        "tool_error": None,
    }


def test_an_untruncated_success_keeps_its_exact_payload() -> None:
    message = _follow_up_tool_message(
        ToolExecutionResult(
            tool_name="file.read",
            ok=True,
            content="short",
            data={"truncated": False, "returned_length": 5, "total_length": 5},
            call_id="call-1",
        )
    )

    expected = '{"status": "success", "output": "short", "error": null}'
    assert message.content == expected
    assert provider_history_payload(message) == {
        "role": "tool",
        "content": expected,
        "tool_call_id": "call-1",
        "tool_status": "success",
        "tool_output": {"truncated": False, "returned_length": 5, "total_length": 5},
        "tool_error": None,
    }


def test_an_approval_denial_keeps_exact_details_and_no_truncation() -> None:
    error = {
        "code": "POLICY_DENIED",
        "message": "approval denied",
        "details": {"approval_id": "apr-7", "decision": "deny"},
    }
    message = _follow_up_tool_message(
        ToolExecutionResult(
            tool_name="file.read",
            ok=False,
            content="",
            error="approval denied",
            data={
                "error_code": "POLICY_DENIED",
                "error_details": {"approval_id": "apr-7", "decision": "deny"},
                "truncated": True,
                "returned_length": 0,
            },
            call_id="call-1",
        )
    )

    assert message.tool_error == error
    assert message.tool_status == "error"
    assert message.tool_output is None
    assert provider_history_payload(message) == {
        "role": "tool",
        "content": message.content,
        "tool_call_id": "call-1",
        "tool_status": "error",
        "tool_output": None,
        "tool_error": error,
    }
    assert json.loads(message.content) == {
        "status": "error",
        "output": None,
        "error": error,
    }


def test_a_retry_failure_keeps_its_exact_retry_details() -> None:
    error = {
        "code": "RATE_LIMITED",
        "message": "provider rate limited the call",
        "details": {"retry_after_ms": 1500},
    }
    message = _follow_up_tool_message(
        ToolExecutionResult(
            tool_name="file.read",
            ok=False,
            content="",
            error="provider rate limited the call",
            data={
                "error_code": "RATE_LIMITED",
                "error_details": {"retry_after_ms": 1500},
            },
            call_id="call-1",
        )
    )

    assert message.tool_error == error
    assert provider_history_payload(message) == {
        "role": "tool",
        "content": message.content,
        "tool_call_id": "call-1",
        "tool_status": "error",
        "tool_output": None,
        "tool_error": error,
    }
    assert json.loads(message.content) == {
        "status": "error",
        "output": None,
        "error": error,
    }
