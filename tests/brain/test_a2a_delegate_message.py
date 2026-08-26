from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.adapters.a2a import _delegate_message_from_payload
from openminion.modules.brain.adapters.a2a.runtime import A2actlAdapter


def test_delegate_message_from_payload_preserves_authored_context_when_goal_present() -> (
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

    assert message == (
        "tell me the current UTC time\n\n"
        "Context:\n"
        "Parent goal: delegate to alibaba-kimi-k2-5 and tell me the current UTC time"
    )


def test_delegate_message_from_payload_preserves_multiline_context() -> None:
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
        "tell me the current UTC time\n\n"
        "Context:\n"
        "Parent goal: delegate to alibaba-kimi-k2-5 and tell me the current UTC time\n"
        "Latest result: previous attempt timed out"
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


def test_configured_agent_handler_forwards_child_turn_controls() -> None:
    approval_callback = object()
    calls: list[dict[str, object]] = []

    class _RuntimeHandle:
        def run_turn(self, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            return {
                "body": "child completed",
                "metadata": {"session_id": "child-session", "run_id": "run-1"},
            }

    runtime_handle = _RuntimeHandle()
    adapter = A2actlAdapter(
        agent_id="parent",
        runtime_resolver=lambda: runtime_handle,
        approval_callback=approval_callback,
    )
    handler = adapter._configured_agent_handler(agent_id="worker")

    payload = handler(
        SimpleNamespace(
            params={"goal": "write a file", "permission_mode": "ask"},
            meta={"session_id": "parent-session"},
            msg_id="msg-1",
            trace_id="trace-1",
            from_agent="parent",
            timeout_ms=137_000,
        )
    )

    assert payload["body"] == "child completed"
    assert len(calls) == 1
    assert calls[0]["approval_callback"] is approval_callback
    assert calls[0]["payload"]["timeout_seconds"] == 137
