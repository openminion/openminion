from __future__ import annotations

import base64
import json
from types import SimpleNamespace

from openminion.base.types import Message as HistoryMessage
from openminion.modules.brain.loop.tools.messages import action_result_to_tool_message
from openminion.modules.brain.schemas import ActionResult, new_uuid
from openminion.modules.llm.client_call import (
    latest_prompt_and_history,
    llm_response_kwargs,
    normalized_messages,
)
from openminion.modules.llm.providers.anthropic.payloads import _messages_anthropic
from openminion.modules.llm.providers.contracts import ProviderResponse
from openminion.modules.llm.providers.message_payloads import _messages_openai_like
from openminion.modules.llm.schemas import LLMRequest, Message, ToolCall
from openminion.modules.llm.transcript import validate_tool_transcript
from openminion.modules.tool.contracts import ProviderToolCall
from openminion.services.agent.context.history import _map_history_to_provider


def test_tool_only_provider_response_retains_assistant_call_owner() -> None:
    response = ProviderResponse(
        text="",
        model="adapter-neutral-model",
        tool_calls=[
            ProviderToolCall(
                id="call-1",
                name="file.read",
                arguments={"path": "/tmp/example.txt"},
                depends_on=["call-0"],
            )
        ],
    )

    payload = llm_response_kwargs(
        resp=response,
        req=SimpleNamespace(model="adapter-neutral-model"),
        client_name="adapter-neutral",
        structured_fields={},
        trace_context={},
    )

    assert payload["assistant_messages"] == [
        Message(
            role="assistant",
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "file.read",
                    "arguments": {"path": "/tmp/example.txt"},
                    "depends_on": ["call-0"],
                }
            ],
        )
    ]


def test_provider_calls_without_ids_receive_unique_batch_ids() -> None:
    payload = llm_response_kwargs(
        resp=ProviderResponse(
            text="",
            model="adapter-neutral-model",
            tool_calls=[
                ProviderToolCall(id="", name="file.read", arguments={"path": "a"}),
                ProviderToolCall(id="", name="file.read", arguments={"path": "b"}),
            ],
        ),
        req=SimpleNamespace(model="adapter-neutral-model"),
        client_name="adapter-neutral",
        structured_fields={},
        trace_context={},
    )
    calls = payload["tool_calls"]
    messages = [
        *payload["assistant_messages"],
        Message(
            role="tool",
            content="a",
            tool_call_id=calls[0].id,
            tool_status="success",
        ),
        Message(
            role="tool",
            content="b",
            tool_call_id=calls[1].id,
            tool_status="success",
        ),
    ]

    assert [call.id for call in calls] == ["call_1", "call_2"]
    assert validate_tool_transcript(LLMRequest(messages=messages)) == (
        "canonical_events"
    )


def test_provider_recovery_marker_survives_llm_response_conversion() -> None:
    payload = llm_response_kwargs(
        resp=ProviderResponse(
            text="display fallback",
            model="adapter-neutral-model",
            normalization={"empty_payload_recovered": True, "ignored": "value"},
        ),
        req=SimpleNamespace(model="adapter-neutral-model"),
        client_name="adapter-neutral",
        structured_fields={},
        trace_context={},
    )

    assert payload["empty_payload_recovered"] is True
    assert "normalization" not in payload


def test_openai_like_renderer_uses_assistant_call_as_argument_owner() -> None:
    request = LLMRequest(
        messages=[
            Message(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "file.read",
                        "arguments": {"path": "/tmp/example.txt"},
                    }
                ],
            ),
            Message(
                role="tool",
                content=json.dumps({"status": "success", "output": "hello"}),
                tool_call_id="call-1",
                tool_status="success",
            ),
        ]
    )

    rendered = _messages_openai_like(request, include_fallback_instruction=False)

    assert rendered == [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "file.read",
                        "arguments": '{"path": "/tmp/example.txt"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": '{"status": "success", "output": "hello"}',
            "tool_call_id": "call-1",
        },
    ]


def _screenshot_tool_request() -> LLMRequest:
    image_data = base64.b64encode(b"png" * 1000).decode("ascii")
    tool_message = action_result_to_tool_message(
        "browser-call-1",
        "browser",
        ActionResult(
            command_id=new_uuid(),
            status="success",
            summary="screenshot captured",
            outputs={
                "artifact": {
                    "kind": "screenshot",
                    "path": "",
                    "mime": "image/png",
                    "content_base64": image_data,
                }
            },
        ),
    )
    return LLMRequest(
        messages=[
            Message(
                role="assistant",
                tool_calls=[
                    {
                        "id": "browser-call-1",
                        "name": "browser",
                        "arguments": {"op": "tab.screenshot", "tab_id": "tab-1"},
                    }
                ],
            ),
            tool_message,
        ]
    )


def test_openai_like_screenshot_tool_result_respects_vision_setting() -> None:
    request = _screenshot_tool_request()

    text_only = _messages_openai_like(
        request,
        include_fallback_instruction=False,
        enable_vision_input=False,
        supports_vision_input=True,
    )
    with_vision = _messages_openai_like(
        request,
        include_fallback_instruction=False,
        enable_vision_input=True,
        supports_vision_input=True,
    )

    assert [message["role"] for message in text_only] == ["assistant", "tool"]
    assert text_only[1]["content"] == request.messages[1].content
    assert [message["role"] for message in with_vision] == [
        "assistant",
        "tool",
        "user",
    ]
    assert with_vision[1]["content"] == request.messages[1].content
    image = with_vision[2]["content"][0]
    assert image["type"] == "image_url"
    expected_data = request.messages[1].content_parts[1].data_base64
    assert expected_data is not None and len(expected_data) > 1200
    assert image["image_url"]["url"] == f"data:image/png;base64,{expected_data}"


def test_openai_like_screenshot_waits_for_parallel_tool_result_batch() -> None:
    request = _screenshot_tool_request()
    request.messages[0].tool_calls.append(
        ToolCall(
            id="read-call-1",
            name="file.read",
            arguments={"path": "README.md"},
        )
    )
    request.messages.append(
        Message(
            role="tool",
            content='{"status":"success","content":"ok"}',
            tool_call_id="read-call-1",
            tool_status="success",
        )
    )

    rendered = _messages_openai_like(
        request,
        include_fallback_instruction=False,
        enable_vision_input=True,
        supports_vision_input=True,
    )

    assert [message["role"] for message in rendered] == [
        "assistant",
        "tool",
        "tool",
        "user",
    ]
    assert rendered[1]["tool_call_id"] == "browser-call-1"
    assert rendered[2]["tool_call_id"] == "read-call-1"
    assert rendered[3]["content"][0]["type"] == "image_url"


def test_anthropic_screenshot_tool_result_includes_inline_image_when_enabled() -> None:
    request = _screenshot_tool_request()
    _system, messages = _messages_anthropic(
        request,
        include_fallback_instruction=False,
        enable_vision_input=True,
        supports_vision_input=True,
    )

    tool_result = messages[1]["content"][0]
    assert tool_result["type"] == "tool_result"
    image = tool_result["content"][1]
    assert image["type"] == "image"
    assert image["source"]["type"] == "base64"
    assert image["source"]["media_type"] == "image/png"
    assert image["source"]["data"] == request.messages[1].content_parts[1].data_base64


def test_normalized_messages_keeps_empty_structured_assistant_turn() -> None:
    request = LLMRequest(
        messages=[
            Message(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "time.current",
                        "arguments": {},
                    }
                ],
            )
        ]
    )

    assert normalized_messages(request) == [
        (
            "assistant",
            "",
            {
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "time.current",
                        "arguments": {},
                    }
                ]
            },
        )
    ]


def test_provider_history_keeps_structured_tool_fields_out_of_metadata() -> None:
    latest, history = latest_prompt_and_history(
        conversational=[
            (
                "assistant",
                "",
                {
                    "transcript_lane": "canonical_events",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "time.current",
                            "arguments": {},
                        }
                    ],
                },
            ),
            (
                "tool",
                '{"status":"success","output":"now"}',
                {
                    "transcript_lane": "canonical_events",
                    "tool_call_id": "call-1",
                    "tool_status": "success",
                    "tool_output": "now",
                },
            ),
        ],
        metadata={"user_input": "What time is it?"},
    )

    assert latest
    assert history[0].tool_calls[0].id == "call-1"
    assert history[0].meta == {"transcript_lane": "canonical_events"}
    assert history[1].tool_call_id == "call-1"
    assert history[1].tool_status == "success"
    assert history[1].tool_output == "now"
    assert history[1].meta == {"transcript_lane": "canonical_events"}


def test_provider_history_preserves_user_before_completed_tool_exchange() -> None:
    latest, history = latest_prompt_and_history(
        conversational=[
            ("user", "Inspect the file.", {}),
            (
                "assistant",
                "",
                {
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "file.read",
                            "arguments": {"path": "/tmp/example.txt"},
                        }
                    ]
                },
            ),
            (
                "tool",
                '{"status":"success","output":"hello"}',
                {"tool_call_id": "call-1", "tool_status": "success"},
            ),
        ],
        metadata={"user_input": "Inspect the file."},
    )

    assert latest
    assert [message.role for message in history] == ["user", "assistant", "tool"]
    assert history[1].tool_calls[0].id == "call-1"
    assert history[2].tool_call_id == "call-1"


def test_agent_history_preserves_tool_role_and_linkage() -> None:
    history = [
        HistoryMessage(
            channel="session",
            target="agent",
            body='{"status":"success","output":"hello"}',
            metadata={
                "role": "tool",
                "tool_call_id": "call-1",
                "tool_status": "success",
            },
        )
    ]

    mapped = _map_history_to_provider(history)

    assert mapped[0].role == "tool"
    assert mapped[0].meta == {
        "tool_call_id": "call-1",
        "tool_status": "success",
    }


def test_legacy_openai_like_reconstruction_remains_explicitly_characterized() -> None:
    request = LLMRequest(
        messages=[
            Message(
                role="tool",
                content='{"status":"success","output":"hello"}',
                meta={
                    "transcript_lane": "legacy_history",
                    "tool_call_id": "legacy-call-1",
                    "tool_name": "file.read",
                    "tool_arguments": {"path": "/tmp/legacy.txt"},
                },
            )
        ]
    )

    rendered = _messages_openai_like(request, include_fallback_instruction=False)

    assert [message["role"] for message in rendered] == ["assistant", "tool"]
    assert rendered[0]["tool_calls"][0]["id"] == "legacy-call-1"
    assert rendered[0]["tool_calls"][0]["function"]["name"] == "file.read"
    assert rendered[1]["tool_call_id"] == "legacy-call-1"
