from __future__ import annotations

import pytest

from openminion.modules.llm.schemas import LLMRequest, Message
from openminion.modules.llm.transcript import (
    ToolTranscriptError,
    validate_tool_transcript,
)
from openminion.modules.llm.runtime.client import LLMCTL


def _call(call_id: str = "call-1") -> Message:
    return Message(
        role="assistant",
        tool_calls=[
            {
                "id": call_id,
                "name": "file.read",
                "arguments": {"path": "/tmp/example.txt"},
            }
        ],
    )


def _result(call_id: str = "call-1") -> Message:
    return Message(
        role="tool",
        content='{"status":"success","output":"hello"}',
        tool_call_id=call_id,
        tool_status="success",
        tool_output="hello",
    )


def test_canonical_transcript_passes() -> None:
    request = LLMRequest(messages=[_call(), _result()])

    assert validate_tool_transcript(request) == "canonical_events"


@pytest.mark.parametrize(
    ("messages", "reason_code"),
    [
        ([_result()], "orphan_result"),
        ([_call("same"), _call("same")], "duplicate_call_id"),
        ([_call(), _result(), _result()], "duplicate_result"),
        (
            [
                _call(),
                _result().model_copy(
                    update={"meta": {"tool_arguments": {"path": "duplicate"}}}
                ),
            ],
            "result_argument_duplication",
        ),
        (
            [_call(), _result().model_copy(update={"tool_status": None})],
            "missing_result_status",
        ),
    ],
)
def test_canonical_transcript_fails_closed(
    messages: list[Message], reason_code: str
) -> None:
    with pytest.raises(ToolTranscriptError) as exc_info:
        validate_tool_transcript(LLMRequest(messages=messages))

    assert exc_info.value.reason_code == reason_code


def test_legacy_result_is_bounded_and_observable() -> None:
    request = LLMRequest(
        messages=[
            Message(
                role="tool",
                content="historical result",
                meta={
                    "transcript_lane": "legacy_history",
                    "tool_call_id": "old-call",
                    "tool_name": "file.read",
                    "tool_arguments": {"path": "/tmp/old.txt"},
                },
            )
        ]
    )

    assert validate_tool_transcript(request) == "legacy_history"


def test_declared_fallback_lane_is_observable() -> None:
    request = LLMRequest(
        messages=[Message(role="user", content="continue")],
        metadata={"tool_call_strategy": "fallback"},
    )

    assert validate_tool_transcript(request) == "declared_fallback"


class _Telemetry:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str, dict[str, object]]] = []

    def emit_canonical_event(
        self,
        session_id: str,
        turn_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        self.events.append((session_id, turn_id, event_type, payload))


def test_request_boundary_rejects_corruption_and_emits_bounded_reason() -> None:
    telemetry = _Telemetry()
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {"default_provider": "unused", "default_model": "unused"},
            "providers": {"unused": {}},
        },
        telemetryctl=telemetry,
    )

    response = runtime.client().complete(
        messages=[_result()],
        metadata={"session_id": "session-1", "turn_id": "turn-1"},
    )

    assert response.ok is False
    assert response.error is not None
    assert response.error.details == {"reason_code": "orphan_result"}
    assert telemetry.events == [
        (
            "session-1",
            "turn-1",
            "llm.tool_transcript.rejected",
            {
                "module_id": "openminion-llm",
                "reason_code": "orphan_result",
                "transcript_lane": "canonical_events",
            },
        )
    ]


def test_stream_boundary_rejects_corruption_before_provider_resolution() -> None:
    telemetry = _Telemetry()
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {"default_provider": "unused", "default_model": "unused"},
            "providers": {"unused": {}},
        },
        telemetryctl=telemetry,
    )

    events = list(
        runtime.client().stream(
            messages=[_result()],
            metadata={"session_id": "session-2", "turn_id": "turn-2"},
        )
    )

    assert [event.type for event in events] == ["error", "done"]
    assert events[0].error is not None
    assert events[0].error.details == {"reason_code": "orphan_result"}
    assert telemetry.events[0][2:] == (
        "llm.tool_transcript.rejected",
        {
            "module_id": "openminion-llm",
            "reason_code": "orphan_result",
            "transcript_lane": "canonical_events",
        },
    )
