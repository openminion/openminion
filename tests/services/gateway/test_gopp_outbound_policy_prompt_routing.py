from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

from openminion.modules.brain.constants import (
    RESPOND_KIND_ASSISTANT,
    RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
)
from openminion.modules.storage.runtime.session_store import (
    EventRecord,
    MessageRecord,
)


@dataclass
class _RecordingSessions:
    append_message_calls: list[dict[str, Any]] = field(default_factory=list)
    append_event_calls: list[dict[str, Any]] = field(default_factory=list)

    def append_message(self, **kwargs: Any) -> MessageRecord:
        self.append_message_calls.append(dict(kwargs))
        return MessageRecord(
            id=f"msg-{len(self.append_message_calls)}",
            session_id=kwargs.get("session_id", "?"),
            conversation_id=kwargs.get("conversation_id") or "",
            thread_id=kwargs.get("thread_id") or "",
            attach_id=kwargs.get("attach_id") or "",
            role=kwargs.get("role", "?"),
            body=kwargs.get("body", ""),
            metadata=dict(kwargs.get("metadata") or {}),
            created_at="2026-05-29T00:00:00Z",
        )

    def append_event(self, **kwargs: Any) -> EventRecord:
        self.append_event_calls.append(dict(kwargs))
        return EventRecord(
            id=len(self.append_event_calls),
            session_id=kwargs.get("session_id", "?"),
            event_type=kwargs.get("event_type", "?"),
            payload=dict(kwargs.get("payload") or {}),
            created_at="2026-05-29T00:00:00Z",
        )


@dataclass
class _MockOutbound:
    body: str
    metadata: dict[str, str]


def _apply_routing(
    sessions: _RecordingSessions,
    outbound: _MockOutbound,
    *,
    session_id: str = "s-gopp",
    conversation_id: str | None = "conv-1",
    thread_id: str | None = "thread-1",
    attach_id: str | None = "attach-1",
    run_id: str = "run-1",
    normalized_request_id: str = "req-1",
    agent_id: str = "agent-gopp",
) -> MessageRecord:
    respond_kind = str(outbound.metadata.get("respond_kind", "")).strip()
    if respond_kind == RESPOND_KIND_POLICY_CONFIRMATION_PROMPT:
        event_record = sessions.append_event(
            session_id=session_id,
            event_type=SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
            payload={
                "body": outbound.body,
                "conversation_id": conversation_id or "",
                "thread_id": thread_id or "",
                "attach_id": attach_id or "",
                "run_id": run_id,
                "request_id": normalized_request_id,
            },
        )
        return MessageRecord(
            id=f"event-{event_record.id}",
            session_id=session_id,
            conversation_id=conversation_id or "",
            thread_id=thread_id or "",
            attach_id=attach_id or "",
            role="event",
            body=outbound.body,
            metadata=dict(outbound.metadata),
            created_at=event_record.created_at,
        )
    return sessions.append_message(
        session_id=session_id,
        conversation_id=conversation_id or None,
        thread_id=thread_id or None,
        attach_id=attach_id or None,
        role="outbound",
        body=outbound.body,
        metadata=outbound.metadata,
        participant_id=agent_id,
        participant_type="agent",
        display_name=agent_id,
    )


def test_default_kind_writes_outbound_message_to_transcript() -> None:
    sessions = _RecordingSessions()
    outbound = _MockOutbound(
        body="Here is your answer.",
        metadata={"respond_kind": RESPOND_KIND_ASSISTANT, "model": "x"},
    )

    record = _apply_routing(sessions, outbound)

    assert len(sessions.append_message_calls) == 1
    assert sessions.append_event_calls == []
    call = sessions.append_message_calls[0]
    assert call["role"] == "outbound"
    assert call["body"] == "Here is your answer."
    assert record.id.startswith("msg-")
    assert record.role == "outbound"


def test_missing_kind_metadata_defaults_to_append_message() -> None:
    sessions = _RecordingSessions()
    outbound = _MockOutbound(
        body="Plain reply.",
        metadata={"model": "x"},  # no respond_kind
    )

    record = _apply_routing(sessions, outbound)

    assert len(sessions.append_message_calls) == 1
    assert sessions.append_event_calls == []
    assert record.role == "outbound"


def test_policy_confirmation_kind_routes_to_typed_event() -> None:
    sessions = _RecordingSessions()
    prose = (
        "Policy confirmation required.\n"
        "file.write (path=/tmp/foo.txt)\n"
        "Reply exactly yes to confirm or exactly no to cancel."
    )
    outbound = _MockOutbound(
        body=prose,
        metadata={
            "respond_kind": RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
            "model": "x",
        },
    )

    record = _apply_routing(sessions, outbound)

    assert sessions.append_message_calls == []
    assert len(sessions.append_event_calls) == 1
    event = sessions.append_event_calls[0]
    assert event["event_type"] == SESSION_EVENT_POLICY_CONFIRMATION_PROMPT
    assert event["payload"]["body"] == prose
    assert event["payload"]["conversation_id"] == "conv-1"
    assert record.id.startswith("event-")
    assert record.role == "event"
    assert record.body == prose


def test_policy_confirmation_kind_skips_transcript_for_broad_tools() -> None:
    sessions = _RecordingSessions()
    for tool_name, args_preview in (
        ("file.write", "path=/tmp/a.txt"),
        ("file.delete", "path=/tmp/b"),
        ("exec.run", "cmd=rm -rf .cache"),
    ):
        prose = (
            "Policy confirmation required.\n"
            f"{tool_name} ({args_preview})\n"
            "Reply exactly yes to confirm or exactly no to cancel."
        )
        outbound = _MockOutbound(
            body=prose,
            metadata={
                "respond_kind": RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
            },
        )
        _apply_routing(sessions, outbound)

    assert sessions.append_message_calls == []
    assert len(sessions.append_event_calls) == 3
    for call in sessions.append_event_calls:
        assert call["event_type"] == SESSION_EVENT_POLICY_CONFIRMATION_PROMPT


def test_policy_confirmation_metadata_attached_to_record() -> None:
    sessions = _RecordingSessions()
    outbound = _MockOutbound(
        body="Reply exactly yes to confirm or exactly no to cancel.",
        metadata={
            "respond_kind": RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
            "run_state": "completed",
            "session_id": "s-gopp",
        },
    )

    record = _apply_routing(sessions, outbound)

    assert record.metadata["respond_kind"] == RESPOND_KIND_POLICY_CONFIRMATION_PROMPT
    assert record.metadata["run_state"] == "completed"


def test_gateway_module_imports_respond_kind_constants() -> None:
    import openminion.services.gateway.turn as gateway_turn

    assert hasattr(gateway_turn, "RESPOND_KIND_POLICY_CONFIRMATION_PROMPT")
    assert (
        gateway_turn.RESPOND_KIND_POLICY_CONFIRMATION_PROMPT
        == RESPOND_KIND_POLICY_CONFIRMATION_PROMPT
    )
    assert hasattr(gateway_turn, "SESSION_EVENT_POLICY_CONFIRMATION_PROMPT")
    assert (
        gateway_turn.SESSION_EVENT_POLICY_CONFIRMATION_PROMPT
        == SESSION_EVENT_POLICY_CONFIRMATION_PROMPT
    )


def test_postprocess_propagates_step_out_kind_into_metadata() -> None:
    import openminion.services.brain.post_execution.postprocess as pp

    source = open(pp.__file__).read()
    assert 'metadata["respond_kind"]' in source
    assert 'getattr(step_out, "kind"' in source


def test_step_output_carries_kind_field() -> None:
    from openminion.modules.brain.schemas import StepOutput

    fields = StepOutput.model_fields
    assert "kind" in fields, "StepOutput must declare a `kind` field"
    assert fields["kind"].default == RESPOND_KIND_ASSISTANT


def test_sessions_double_can_be_substituted_with_magicmock() -> None:
    sessions = MagicMock()
    sessions.append_event.return_value = EventRecord(
        id=42,
        session_id="s",
        event_type=SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
        payload={},
        created_at="2026-05-29T00:00:00Z",
    )
    outbound = _MockOutbound(
        body="x", metadata={"respond_kind": RESPOND_KIND_POLICY_CONFIRMATION_PROMPT}
    )
    _apply_routing(sessions, outbound)
    sessions.append_event.assert_called_once()
    sessions.append_message.assert_not_called()
