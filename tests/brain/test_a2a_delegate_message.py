from __future__ import annotations

from openminion.modules.brain.adapters.a2a import _delegate_message_from_payload


def test_delegate_message_from_payload_omits_parent_goal_context_when_goal_present() -> (
    None
):
    message = _delegate_message_from_payload(
        {
            "goal": "tell me the current UTC time",
            "summary": (
                "Parent goal: delegate to alibaba-kimi-k2-5 and tell me the current UTC time"
            ),
        }
    )

    assert message == "tell me the current UTC time"


def test_delegate_message_from_payload_keeps_non_parent_context_lines() -> None:
    message = _delegate_message_from_payload(
        {
            "goal": "tell me the current UTC time",
            "summary": (
                "Parent goal: delegate to alibaba-kimi-k2-5 and tell me the current UTC time\n"
                "Latest result: previous attempt timed out"
            ),
        }
    )

    assert message == (
        "tell me the current UTC time\n\nContext:\nLatest result: previous attempt timed out"
    )


def test_delegate_message_from_payload_includes_typed_parent_context_block() -> None:
    message = _delegate_message_from_payload(
        {
            "goal": "validate the retry tests",
            "delegation_context": {
                "summary": "Parent isolated the failing retry path.",
                "artifacts": ["artifact://retry-log"],
                "intent_id": "intent-retry",
            },
        }
    )

    assert "[PARENT CONTEXT]" in message
    assert "summary: Parent isolated the failing retry path." in message
    assert "artifacts: artifact://retry-log" in message
    assert "intent_id: intent-retry" in message
