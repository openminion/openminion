from __future__ import annotations

import json
from types import SimpleNamespace

from openminion.base.types import Message as HistoryMessage
from openminion.modules.llm.client_call import (
    latest_prompt_and_history,
    llm_response_kwargs,
    normalized_messages,
)
from openminion.modules.llm.providers.contracts import ProviderResponse
from openminion.modules.llm.providers.message_payloads import _messages_openai_like
from openminion.modules.llm.schemas import LLMRequest, Message
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
