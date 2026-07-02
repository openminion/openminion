from __future__ import annotations

import pytest

from openminion.services.runtime.turn_input import (
    QUEUE_EVENT_CANCEL_ACKNOWLEDGED,
    QUEUE_EVENT_DEQUEUED,
    QUEUE_EVENT_ENQUEUED,
    QUEUE_EVENT_FULL,
    TurnInputIntent,
    TurnInputQueue,
    TurnInputQueueError,
    TurnInputQueueStatus,
)


def _queue() -> TurnInputQueue:
    ids = iter(["q1", "q2", "q3", "q4"])
    return TurnInputQueue(max_pending_per_session=2, id_factory=lambda: next(ids))


def test_enqueue_list_fifo_and_redacted_preview() -> None:
    queue = _queue()
    first = queue.enqueue(session_id="s1", agent_id="a1", text="first secret line")
    second = queue.enqueue(session_id="s1", agent_id="a1", text="second")

    assert [entry.queue_id for entry in queue.list_entries(session_id="s1")] == [
        first.queue_id,
        second.queue_id,
    ]
    assert first.event_payload()["text_preview"] == "first secret line"
    assert "text" not in first.event_payload()


def test_idempotency_returns_existing_entry() -> None:
    queue = _queue()
    first = queue.enqueue(
        session_id="s1", agent_id="a1", text="first", idempotency_key="idem"
    )
    second = queue.enqueue(
        session_id="s1", agent_id="a1", text="changed", idempotency_key="idem"
    )

    assert second is first
    assert queue.list_entries(session_id="s1")[0].text == "first"


def test_queue_full_is_structured_error() -> None:
    queue = _queue()
    queue.enqueue(session_id="s1", agent_id="a1", text="first")
    queue.enqueue(session_id="s1", agent_id="a1", text="second")

    with pytest.raises(TurnInputQueueError) as exc_info:
        queue.enqueue(session_id="s1", agent_id="a1", text="third")

    assert exc_info.value.code == "QUEUE_FULL"
    assert exc_info.value.details["max_pending"] == 2


def test_drop_requires_current_status_version() -> None:
    queue = _queue()
    entry = queue.enqueue(session_id="s1", agent_id="a1", text="first")

    with pytest.raises(TurnInputQueueError) as exc_info:
        queue.drop(session_id="s1", queue_id=entry.queue_id, status_version=99)

    assert exc_info.value.code == "QUEUE_CONFLICT"

    dropped = queue.drop(
        session_id="s1", queue_id=entry.queue_id, status_version=entry.status_version
    )
    assert dropped.status == TurnInputQueueStatus.DROPPED
    assert dropped.status_version == entry.status_version + 1


def test_move_supports_before_after_and_conflicts() -> None:
    queue = _queue()
    first = queue.enqueue(session_id="s1", agent_id="a1", text="first")
    second = queue.enqueue(session_id="s1", agent_id="a1", text="second")

    moved = queue.move(
        session_id="s1",
        queue_id=second.queue_id,
        before_queue_id=first.queue_id,
        status_version=second.status_version,
    )

    assert moved.status_version == second.status_version + 1
    assert [entry.queue_id for entry in queue.list_entries(session_id="s1")] == [
        second.queue_id,
        first.queue_id,
    ]

    with pytest.raises(TurnInputQueueError) as exc_info:
        queue.move(
            session_id="s1",
            queue_id=first.queue_id,
            before_queue_id=second.queue_id,
            after_queue_id=second.queue_id,
            status_version=first.status_version,
        )
    assert exc_info.value.code == "INVALID_MOVE_REQUEST"


def test_reserve_next_and_terminal_status() -> None:
    queue = _queue()
    entry = queue.enqueue(session_id="s1", agent_id="a1", text="first")

    reserved = queue.reserve_next(session_id="s1", agent_id="a1")
    assert reserved is not None
    assert reserved.queue_id == entry.queue_id
    assert reserved.status == TurnInputQueueStatus.RESERVED

    queue.mark_running(queue_id=reserved.queue_id, trace_id="trace-1")
    running = queue.list_entries(session_id="s1")[0]
    assert running.status == TurnInputQueueStatus.RUNNING
    assert running.trace_id == "trace-1"

    queue.mark_terminal(queue_id=reserved.queue_id, status=TurnInputQueueStatus.COMPLETED)
    completed = queue.list_entries(session_id="s1")[0]
    assert completed.status == TurnInputQueueStatus.COMPLETED


def test_reserve_next_detects_changed_head() -> None:
    queue = _queue()
    first = queue.enqueue(session_id="s1", agent_id="a1", text="first")
    queue.enqueue(session_id="s1", agent_id="a1", text="second")

    with pytest.raises(TurnInputQueueError) as exc_info:
        queue.reserve_next(session_id="s1", agent_id="a1", expected_queue_id="other")

    assert exc_info.value.code == "QUEUE_CONFLICT"
    assert exc_info.value.details["actual_queue_id"] == first.queue_id


def test_steer_current_is_deferred_without_semantic_inference() -> None:
    queue = _queue()
    entry = queue.enqueue(
        session_id="s1",
        agent_id="a1",
        text="actually please steer this turn",
        intent=TurnInputIntent.STEER_CURRENT,
    )

    assert entry.status == TurnInputQueueStatus.STEER_DEFERRED
    assert entry.metadata["reason"] == "steer_current_unsupported_v1"
    assert entry.text == "actually please steer this turn"


def test_operational_events_are_emitted_without_text_payload() -> None:
    events: list[tuple[str, dict]] = []
    queue = TurnInputQueue(
        max_pending_per_session=1,
        id_factory=lambda: f"q{len(events) + 1}",
        on_event=lambda event_type, payload: events.append((event_type, payload)),
    )

    entry = queue.enqueue(session_id="s1", agent_id="a1", text="secret text")
    reserved = queue.reserve_next(session_id="s1", agent_id="a1")
    assert reserved is not None
    queue.mark_terminal(
        queue_id=reserved.queue_id,
        status=TurnInputQueueStatus.CANCELLED,
    )

    assert [event_type for event_type, _payload in events] == [
        QUEUE_EVENT_ENQUEUED,
        QUEUE_EVENT_DEQUEUED,
        QUEUE_EVENT_CANCEL_ACKNOWLEDGED,
    ]
    assert events[0][1]["queue_id"] == entry.queue_id
    assert "text" not in events[0][1]
    assert events[0][1]["text_preview"] == "secret text"


def test_queue_full_emits_operational_signal() -> None:
    events: list[tuple[str, dict]] = []
    queue = TurnInputQueue(
        max_pending_per_session=1,
        id_factory=lambda: "q1",
        on_event=lambda event_type, payload: events.append((event_type, payload)),
    )
    queue.enqueue(session_id="s1", agent_id="a1", text="first")

    with pytest.raises(TurnInputQueueError):
        queue.enqueue(session_id="s1", agent_id="a1", text="second")

    assert events[-1] == (QUEUE_EVENT_FULL, {"session_id": "s1", "max_pending": 1})
