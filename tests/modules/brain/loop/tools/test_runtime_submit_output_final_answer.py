from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.modules.brain.loop.tools.runtime import (
    _normalize_submit_output_final_answer_response,
)
from openminion.modules.llm.schemas import LLMResponse, ToolCall


def _response(*, tool_calls: list[ToolCall]) -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        tool_calls=tool_calls,
        finish_reason="tool_calls",
    )


def test_submit_output_final_answer_close_becomes_typed_final_response() -> None:
    response = _response(
        tool_calls=[
            ToolCall(
                id="call-1",
                name="submit_output",
                arguments={
                    "satisfied": True,
                    "next_action": "close",
                    "final_answer": "Created the project and verified pytest.",
                    "reason": "all success criteria met",
                },
            )
        ]
    )

    normalized = _normalize_submit_output_final_answer_response(response)

    assert normalized.output_text == "Created the project and verified pytest."
    assert [message.content for message in normalized.assistant_messages] == [
        "Created the project and verified pytest."
    ]
    assert normalized.tool_calls == []
    assert normalized.finalization_status == {
        "status": "final_answer",
        "reasoning": "all success criteria met",
        "remaining_work": "",
        "blocking_reason": "",
    }
    assert (
        normalized.telemetry["typed_signal_sources"][STATE_KEY_FINALIZATION_STATUS]
        == "structured_field"
    )


def test_submit_output_final_answer_without_close_signal_stays_tool_call() -> None:
    response = _response(
        tool_calls=[
            ToolCall(
                id="call-1",
                name="submit_output",
                arguments={
                    "satisfied": False,
                    "next_action": "continue",
                    "final_answer": "Partial progress.",
                },
            )
        ]
    )

    assert _normalize_submit_output_final_answer_response(response) is response


def test_submit_output_without_final_answer_stays_tool_call() -> None:
    response = _response(
        tool_calls=[
            ToolCall(
                id="call-1",
                name="submit_output",
                arguments={"satisfied": True, "next_action": "close"},
            )
        ]
    )

    assert _normalize_submit_output_final_answer_response(response) is response


def test_non_submit_output_tool_call_stays_tool_call() -> None:
    response = _response(
        tool_calls=[
            ToolCall(
                id="call-1",
                name="file.write",
                arguments={"path": "README.md", "content": "hello"},
            )
        ]
    )

    assert _normalize_submit_output_final_answer_response(response) is response
