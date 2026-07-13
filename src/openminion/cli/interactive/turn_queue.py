"""Focus client adoption of the shared runtime turn-input queue."""

from __future__ import annotations

from typing import Any, cast

from openminion.cli.interactive.widgets import FocusTranscript
from openminion.cli.presentation.models import ChatMessage, MessageKind
from openminion.cli.presentation.queue import (
    queue_run_next_empty_notice,
    queued_message_notice,
)
from openminion.services.runtime.turn_input import (
    QUEUE_EVENT_DEQUEUED,
    QUEUE_EVENT_ENQUEUED,
    TurnInputQueue,
    TurnInputQueueEntry,
    TurnInputQueueError,
    TurnInputQueueStatus,
)


class FocusTurnQueueMixin:
    def _queue_turn(self, text: str) -> None:
        owner = cast(Any, self)
        try:
            entry = owner._turn_input_queue.enqueue(
                session_id=owner._runtime.session_id,
                agent_id=owner._runtime.agent_id,
                text=text,
                source_client="focus",
            )
        except TurnInputQueueError as exc:
            owner.query_one(FocusTranscript).push_message(
                ChatMessage(kind=MessageKind.ERROR, sender="error", body=str(exc))
            )
            return
        owner._append_focus_queue_event(QUEUE_EVENT_ENQUEUED, entry)
        chat = owner.query_one(FocusTranscript)
        chat.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body=text))
        chat.push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=queued_message_notice(owner._queued_count()),
            )
        )
        owner._push_status_line(state="responding")

    def _resolve_turn_input_queue(self) -> TurnInputQueue:
        owner = cast(Any, self)
        queue = getattr(owner._runtime, "turn_input_queue", None)
        if isinstance(queue, TurnInputQueue):
            return queue
        queue = TurnInputQueue()
        try:
            setattr(owner._runtime, "turn_input_queue", queue)
        except (AttributeError, TypeError):
            pass
        return queue

    def _queued_count(self) -> int:
        owner = cast(Any, self)
        try:
            return int(
                owner._turn_input_queue.pending_count(
                    session_id=owner._runtime.session_id,
                    agent_id=owner._runtime.agent_id,
                )
            )
        except (AttributeError, TurnInputQueueError):
            return 0

    def _append_focus_queue_event(
        self,
        event_type: str,
        entry: TurnInputQueueEntry,
    ) -> None:
        owner = cast(Any, self)
        sessions = getattr(owner._runtime, "sessions", None)
        append_event = getattr(sessions, "append_event", None)
        if not callable(append_event):
            return
        try:
            append_event(
                session_id=entry.session_id,
                event_type=event_type,
                payload=entry.event_payload(),
            )
        except Exception:
            pass

    def _start_turn_worker(
        self,
        text: str,
        *,
        render_user: bool = True,
        queue_id: str | None = None,
    ) -> None:
        owner = cast(Any, self)
        owner._turn_worker = owner.run_worker(
            owner._run_turn(text, render_user=render_user, queue_id=queue_id),
            exclusive=True,
        )

    def _start_next_queued_turn(self, *, expected_queue_id: str | None = None) -> None:
        owner = cast(Any, self)
        if owner._busy or owner._turn_worker is not None:
            return
        try:
            entry = owner._turn_input_queue.reserve_next(
                session_id=owner._runtime.session_id,
                agent_id=owner._runtime.agent_id,
                expected_queue_id=expected_queue_id,
            )
        except TurnInputQueueError as exc:
            owner.query_one(FocusTranscript).push_message(
                ChatMessage(kind=MessageKind.ERROR, sender="error", body=str(exc))
            )
            return
        if entry is None:
            return
        owner._turn_input_queue.mark_running(queue_id=entry.queue_id)
        owner._append_focus_queue_event(QUEUE_EVENT_DEQUEUED, entry)
        owner._start_turn_worker(entry.text, render_user=False, queue_id=entry.queue_id)

    def _mark_queue_entry_terminal(
        self,
        queue_id: str | None,
        *,
        interrupted: bool,
        failed: bool,
    ) -> None:
        if not queue_id:
            return
        owner = cast(Any, self)
        status = TurnInputQueueStatus.COMPLETED
        if interrupted:
            status = TurnInputQueueStatus.CANCELLED
        elif failed:
            status = TurnInputQueueStatus.FAILED
        try:
            owner._turn_input_queue.mark_terminal(queue_id=queue_id, status=status)
        except TurnInputQueueError:
            pass

    async def _cancel_current_and_run_next(self) -> None:
        owner = cast(Any, self)
        if not owner._busy:
            return
        entries = owner._turn_input_queue.list_entries(
            session_id=owner._runtime.session_id,
            agent_id=owner._runtime.agent_id,
            statuses={TurnInputQueueStatus.QUEUED},
        )
        if not entries:
            owner.query_one(FocusTranscript).push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=queue_run_next_empty_notice(),
                )
            )
            return
        owner._cancel_run_next_expected_queue_id = entries[0].queue_id
        owner._run_next_after_interrupt = True
        await owner._interrupt_current_turn()


__all__ = ["FocusTurnQueueMixin"]
