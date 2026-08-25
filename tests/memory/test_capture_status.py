from __future__ import annotations

import logging
from pathlib import Path

from openminion.modules.memory.runtime.capture_status import (
    project_capture_processing,
    summarize_capture_processing,
)
from openminion.modules.memory.gateway_turn import capture_evidence_id
from openminion.modules.session.storage.store import SQLiteSessionStore
from openminion.services.agent.memory.gateway_adapter import (
    DisabledMemoryGatewayAdapter,
)
from openminion.services.gateway.memory import record_memory_turn


def test_capture_projection_keeps_pending_until_terminal_event() -> None:
    events = [
        {
            "event_id": "event-1",
            "event_type": "memory.write.started",
            "created_at": "2026-08-24T00:00:00Z",
            "payload": {
                "capture_evidence_id": "capture:v1:a",
                "patch_id": "patch-a",
                "capture_state": "pending",
            },
        },
        {
            "event_id": "event-2",
            "event_type": "memory.write.started",
            "created_at": "2026-08-24T00:00:01Z",
            "payload": {
                "capture_evidence_id": "capture:v1:b",
                "patch_id": "patch-b",
                "capture_state": "pending",
            },
        },
        {
            "event_id": "event-3",
            "event_type": "memory.write.completed",
            "created_at": "2026-08-24T00:00:02Z",
            "payload": {
                "capture_evidence_id": "capture:v1:b",
                "patch_id": "patch-b",
                "changed": "false",
                "capture_reason": "no_output",
            },
        },
    ]

    projected = project_capture_processing(events)

    assert projected["capture:v1:a"].disposition == "pending"
    assert projected["capture:v1:b"].disposition == "succeeded_no_output"
    assert projected["capture:v1:b"].reason == "no_output"

    summary = summarize_capture_processing(projected)
    assert summary.pending == 1
    assert summary.succeeded_no_output == 1
    assert summary.oldest_pending_at == "2026-08-24T00:00:00Z"


def test_disabled_memory_emits_terminal_content_free_rejection() -> None:
    events: list[dict[str, object]] = []

    def _emit(**kwargs: object) -> None:
        events.append(dict(kwargs))

    record_memory_turn(
        agent_memory=DisabledMemoryGatewayAdapter(agent_id="alpha"),
        logger=logging.getLogger("openminion.tests.capture"),
        agent_id="alpha",
        memory_capsule_strategy="frozen_session",
        memory_capsule_cache={},
        session_id="session-1",
        run_id="run-1",
        request_id="request-1",
        channel="console",
        target="local",
        user_message="private transcript body",
        assistant_message="private response body",
        conversation_id="conversation-1",
        thread_id="thread-1",
        attach_id="",
        emit_memory_event=_emit,
        outbound_metadata={},
    )

    assert [event["event_type"] for event in events] == ["memory.write.rejected"]
    payload = events[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["capture_state"] == "rejected"
    assert payload["capture_reason"] == "memory_disabled"
    assert "private transcript body" not in str(payload)
    assert "private response body" not in str(payload)


def test_backend_none_emits_distinct_terminal_rejection() -> None:
    events: list[dict[str, object]] = []
    adapter = DisabledMemoryGatewayAdapter(agent_id="alpha")
    adapter.disabled_reason = "backend_none"

    record_memory_turn(
        agent_memory=adapter,
        logger=logging.getLogger("openminion.tests.capture"),
        agent_id="alpha",
        memory_capsule_strategy="frozen_session",
        memory_capsule_cache={},
        session_id="session-1",
        run_id="run-1",
        request_id="request-1",
        channel="console",
        target="local",
        user_message="body",
        assistant_message="reply",
        conversation_id="conversation-1",
        thread_id="thread-1",
        attach_id="",
        emit_memory_event=lambda **event: events.append(dict(event)),
        outbound_metadata={},
    )

    payload = events[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["capture_reason"] == "backend_none"


def test_capture_projection_survives_session_store_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    sessions = SQLiteSessionStore(db_path)
    session_id = sessions.create_session(
        initial_agent_id="alpha",
        profile_version="v1",
    )
    sessions.append_event(
        session_id,
        event_type="memory.write.started",
        payload={
            "capture_evidence_id": "capture:v1:restart",
            "capture_state": "pending",
            "patch_id": "patch-restart",
        },
    )

    reopened = SQLiteSessionStore(db_path)
    projected = project_capture_processing(
        reopened.get_events(
            session_id,
            types=["memory.write.started", "memory.write.completed"],
        )
    )

    assert projected["capture:v1:restart"].disposition == "pending"
    assert projected["capture:v1:restart"].patch_id == "patch-restart"


def test_capture_identity_is_stable_for_replayed_turn() -> None:
    first = capture_evidence_id(
        session_id="session-1",
        request_id="request-1",
    )
    replay = capture_evidence_id(
        session_id="session-1",
        request_id="request-1",
    )

    assert first == replay
    assert first.startswith("capture:v1:")
    assert first != capture_evidence_id(
        session_id="session-1",
        request_id="request-2",
    )
