"""Runtime-owned user turn-input queue contracts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from threading import RLock
from collections.abc import Iterable
from typing import Any, Callable
from uuid import uuid4


class TurnInputIntent(StrEnum):
    QUEUE_NEXT = "queue_next"
    STEER_CURRENT = "steer_current"
    CANCEL_CURRENT = "cancel_current"
    CANCEL_CURRENT_AND_RUN_NEXT = "cancel_current_and_run_next"
    DROP_QUEUED = "drop_queued"
    MOVE_QUEUED = "move_queued"


class TurnInputQueueStatus(StrEnum):
    QUEUED = "queued"
    RESERVED = "reserved"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    DROPPED = "dropped"
    STEER_DEFERRED = "steer_deferred"


QUEUE_EVENT_ENQUEUED = "turn_input.enqueued"
QUEUE_EVENT_DEQUEUED = "turn_input.dequeued"
QUEUE_EVENT_DROPPED = "turn_input.dropped"
QUEUE_EVENT_MOVED = "turn_input.moved"
QUEUE_EVENT_CANCEL_REQUESTED = "turn_input.cancel_requested"
QUEUE_EVENT_CANCEL_ACKNOWLEDGED = "turn_input.cancel_acknowledged"
QUEUE_EVENT_CANCEL_FAILED = "turn_input.cancel_failed"
QUEUE_EVENT_STEER_DEFERRED = "turn_input.steer_deferred"
QUEUE_EVENT_FULL = "turn_input.queue_full"
QUEUE_EVENT_COMPLETED = "turn_input.completed"
QUEUE_EVENT_FAILED = "turn_input.failed"


_QUEUE_ACTIVE_STATUSES = {
    TurnInputQueueStatus.QUEUED,
    TurnInputQueueStatus.RESERVED,
    TurnInputQueueStatus.RUNNING,
}


@dataclass(frozen=True)
class TurnInputQueueEntry:
    queue_id: str
    session_id: str
    agent_id: str
    text: str
    intent: TurnInputIntent = TurnInputIntent.QUEUE_NEXT
    status: TurnInputQueueStatus = TurnInputQueueStatus.QUEUED
    source_client: str = "unknown"
    idempotency_key: str | None = None
    priority: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    status_version: int = 1
    created_at: str = field(default_factory=lambda: _utc_now_iso())
    started_at: str | None = None
    completed_at: str | None = None
    trace_id: str | None = None

    def to_dict(self, *, include_text: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "queue_id": self.queue_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "intent": self.intent.value,
            "status": self.status.value,
            "source_client": self.source_client,
            "idempotency_key": self.idempotency_key,
            "priority": self.priority,
            "metadata": dict(self.metadata),
            "status_version": self.status_version,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "trace_id": self.trace_id,
            "text_preview": redact_text_preview(self.text),
        }
        if include_text:
            payload["text"] = self.text
        return payload

    def event_payload(self) -> dict[str, Any]:
        return self.to_dict(include_text=False)


class TurnInputQueueError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class TurnInputQueue:
    """In-memory turn-input queue with auditable entry state."""

    def __init__(
        self,
        *,
        max_pending_per_session: int = 20,
        id_factory: Callable[[], str] | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._max_pending_per_session = max(1, int(max_pending_per_session))
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._on_event = on_event
        self._lock = RLock()
        self._entries: list[TurnInputQueueEntry] = []
        self._idempotency: dict[tuple[str, str, str], str] = {}

    def enqueue(
        self,
        *,
        session_id: str,
        agent_id: str,
        text: str,
        intent: TurnInputIntent | str = TurnInputIntent.QUEUE_NEXT,
        source_client: str = "unknown",
        idempotency_key: str | None = None,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> TurnInputQueueEntry:
        normalized_session_id = _require_non_empty(session_id, "session_id")
        normalized_agent_id = _require_non_empty(agent_id, "agent_id")
        normalized_text = _require_non_empty(text, "text")
        normalized_intent = _coerce_intent(intent)
        queue_status = TurnInputQueueStatus.QUEUED
        entry_metadata = dict(metadata or {})
        if normalized_intent == TurnInputIntent.STEER_CURRENT:
            queue_status = TurnInputQueueStatus.STEER_DEFERRED
            entry_metadata.setdefault("deferred_to", TurnInputIntent.QUEUE_NEXT.value)
            entry_metadata.setdefault("reason", "steer_current_unsupported_v1")
        key = _idempotency_lookup_key(
            normalized_session_id,
            normalized_agent_id,
            idempotency_key,
        )
        with self._lock:
            if key is not None:
                existing_id = self._idempotency.get(key)
                existing = self._find_by_id_locked(existing_id) if existing_id else None
                if existing is not None:
                    return existing
            self._raise_if_full_locked(normalized_session_id)
            entry = TurnInputQueueEntry(
                queue_id=self._id_factory(),
                session_id=normalized_session_id,
                agent_id=normalized_agent_id,
                text=normalized_text,
                intent=normalized_intent,
                status=queue_status,
                source_client=str(source_client or "unknown").strip() or "unknown",
                idempotency_key=(str(idempotency_key).strip() or None)
                if idempotency_key is not None
                else None,
                priority=int(priority or 0),
                metadata=entry_metadata,
            )
            self._entries.append(entry)
            if key is not None:
                self._idempotency[key] = entry.queue_id
            event_type = (
                QUEUE_EVENT_STEER_DEFERRED
                if queue_status == TurnInputQueueStatus.STEER_DEFERRED
                else QUEUE_EVENT_ENQUEUED
            )
            self._emit(event_type, entry.event_payload())
            return entry

    def list_entries(
        self,
        *,
        session_id: str,
        agent_id: str | None = None,
        statuses: Iterable[TurnInputQueueStatus | str] | None = None,
    ) -> list[TurnInputQueueEntry]:
        normalized_session_id = _require_non_empty(session_id, "session_id")
        normalized_agent_id = str(agent_id or "").strip() or None
        normalized_statuses = {_coerce_status(status) for status in statuses or ()}
        with self._lock:
            return [
                entry
                for entry in self._entries
                if entry.session_id == normalized_session_id
                and (
                    normalized_agent_id is None or entry.agent_id == normalized_agent_id
                )
                and (not normalized_statuses or entry.status in normalized_statuses)
            ]

    def drop(
        self,
        *,
        session_id: str,
        queue_id: str,
        status_version: int | None,
    ) -> TurnInputQueueEntry:
        with self._lock:
            index, entry = self._find_for_session_locked(session_id, queue_id)
            self._assert_status_version(entry, status_version)
            if entry.status != TurnInputQueueStatus.QUEUED:
                raise TurnInputQueueError(
                    "QUEUE_CONFLICT",
                    "Only queued entries can be dropped.",
                    {"queue_id": entry.queue_id, "status": entry.status.value},
                )
            updated = replace(
                entry,
                status=TurnInputQueueStatus.DROPPED,
                status_version=entry.status_version + 1,
                completed_at=_utc_now_iso(),
            )
            self._entries[index] = updated
            self._emit(QUEUE_EVENT_DROPPED, updated.event_payload())
            return updated

    def move(
        self,
        *,
        session_id: str,
        queue_id: str,
        status_version: int | None,
        before_queue_id: str | None = None,
        after_queue_id: str | None = None,
    ) -> TurnInputQueueEntry:
        if before_queue_id and after_queue_id:
            raise TurnInputQueueError(
                "INVALID_MOVE_REQUEST",
                "Specify only one of before_queue_id or after_queue_id.",
            )
        with self._lock:
            index, entry = self._find_for_session_locked(session_id, queue_id)
            self._assert_status_version(entry, status_version)
            if entry.status != TurnInputQueueStatus.QUEUED:
                raise TurnInputQueueError(
                    "QUEUE_CONFLICT",
                    "Only queued entries can be moved.",
                    {"queue_id": entry.queue_id, "status": entry.status.value},
                )
            self._entries.pop(index)
            target_index = self._resolve_move_index_locked(
                session_id=session_id,
                before_queue_id=before_queue_id,
                after_queue_id=after_queue_id,
            )
            updated = replace(entry, status_version=entry.status_version + 1)
            self._entries.insert(target_index, updated)
            self._emit(
                QUEUE_EVENT_MOVED,
                {
                    **updated.event_payload(),
                    "before_queue_id": before_queue_id,
                    "after_queue_id": after_queue_id,
                },
            )
            return updated

    def reserve_next(
        self,
        *,
        session_id: str,
        agent_id: str,
        expected_queue_id: str | None = None,
    ) -> TurnInputQueueEntry | None:
        with self._lock:
            for index, entry in enumerate(self._entries):
                if (
                    entry.session_id == session_id
                    and entry.agent_id == agent_id
                    and entry.status == TurnInputQueueStatus.QUEUED
                ):
                    if expected_queue_id and entry.queue_id != expected_queue_id:
                        raise TurnInputQueueError(
                            "QUEUE_CONFLICT",
                            "The next queued entry changed before reservation.",
                            {
                                "expected_queue_id": expected_queue_id,
                                "actual_queue_id": entry.queue_id,
                            },
                        )
                    updated = replace(
                        entry,
                        status=TurnInputQueueStatus.RESERVED,
                        status_version=entry.status_version + 1,
                    )
                    self._entries[index] = updated
                    self._emit(QUEUE_EVENT_DEQUEUED, updated.event_payload())
                    return updated
        return None

    def mark_running(self, *, queue_id: str, trace_id: str | None = None) -> None:
        self._replace_by_id(
            queue_id,
            lambda entry: replace(
                entry,
                status=TurnInputQueueStatus.RUNNING,
                started_at=_utc_now_iso(),
                trace_id=str(trace_id or "").strip() or entry.trace_id,
                status_version=entry.status_version + 1,
            ),
        )

    def mark_terminal(
        self,
        *,
        queue_id: str,
        status: TurnInputQueueStatus | str,
    ) -> None:
        terminal = _coerce_status(status)
        if terminal not in {
            TurnInputQueueStatus.COMPLETED,
            TurnInputQueueStatus.CANCELLED,
            TurnInputQueueStatus.FAILED,
        }:
            raise TurnInputQueueError(
                "INVALID_STATUS", f"Invalid terminal status: {status}"
            )
        self._replace_by_id(
            queue_id,
            lambda entry: replace(
                entry,
                status=terminal,
                completed_at=_utc_now_iso(),
                status_version=entry.status_version + 1,
            ),
        )

    def pending_count(self, *, session_id: str, agent_id: str | None = None) -> int:
        return len(
            self.list_entries(
                session_id=session_id,
                agent_id=agent_id,
                statuses={TurnInputQueueStatus.QUEUED, TurnInputQueueStatus.RESERVED},
            )
        )

    def _raise_if_full_locked(self, session_id: str) -> None:
        pending = sum(
            1
            for entry in self._entries
            if entry.session_id == session_id and entry.status in _QUEUE_ACTIVE_STATUSES
        )
        if pending >= self._max_pending_per_session:
            self._emit(
                QUEUE_EVENT_FULL,
                {
                    "session_id": session_id,
                    "max_pending": self._max_pending_per_session,
                },
            )
            raise TurnInputQueueError(
                "QUEUE_FULL",
                "Turn input queue is full.",
                {
                    "session_id": session_id,
                    "max_pending": self._max_pending_per_session,
                },
            )

    def _find_for_session_locked(
        self, session_id: str, queue_id: str
    ) -> tuple[int, TurnInputQueueEntry]:
        normalized_session_id = _require_non_empty(session_id, "session_id")
        normalized_queue_id = _require_non_empty(queue_id, "queue_id")
        for index, entry in enumerate(self._entries):
            if (
                entry.session_id == normalized_session_id
                and entry.queue_id == normalized_queue_id
            ):
                return index, entry
        raise TurnInputQueueError(
            "QUEUE_ENTRY_NOT_FOUND",
            "Turn input queue entry was not found.",
            {"session_id": normalized_session_id, "queue_id": normalized_queue_id},
        )

    def _find_by_id_locked(self, queue_id: str | None) -> TurnInputQueueEntry | None:
        if not queue_id:
            return None
        for entry in self._entries:
            if entry.queue_id == queue_id:
                return entry
        return None

    def _resolve_move_index_locked(
        self,
        *,
        session_id: str,
        before_queue_id: str | None,
        after_queue_id: str | None,
    ) -> int:
        if not before_queue_id and not after_queue_id:
            return len(self._entries)
        target_id = before_queue_id or after_queue_id or ""
        for index, entry in enumerate(self._entries):
            if entry.session_id == session_id and entry.queue_id == target_id:
                return index if before_queue_id else index + 1
        raise TurnInputQueueError(
            "QUEUE_ENTRY_NOT_FOUND",
            "Move target queue entry was not found.",
            {"session_id": session_id, "target_queue_id": target_id},
        )

    @staticmethod
    def _assert_status_version(
        entry: TurnInputQueueEntry,
        status_version: int | None,
    ) -> None:
        if status_version is None:
            return
        if int(status_version) != entry.status_version:
            raise TurnInputQueueError(
                "QUEUE_CONFLICT",
                "Queue entry status_version is stale.",
                {
                    "queue_id": entry.queue_id,
                    "expected_status_version": entry.status_version,
                    "received_status_version": int(status_version),
                },
            )

    def _replace_by_id(
        self,
        queue_id: str,
        updater: Callable[[TurnInputQueueEntry], TurnInputQueueEntry],
    ) -> None:
        with self._lock:
            for index, entry in enumerate(self._entries):
                if entry.queue_id == queue_id:
                    updated = updater(entry)
                    self._entries[index] = updated
                    if updated.status in {
                        TurnInputQueueStatus.COMPLETED,
                        TurnInputQueueStatus.CANCELLED,
                        TurnInputQueueStatus.FAILED,
                    }:
                        event_type = {
                            TurnInputQueueStatus.COMPLETED: QUEUE_EVENT_COMPLETED,
                            TurnInputQueueStatus.CANCELLED: QUEUE_EVENT_CANCEL_ACKNOWLEDGED,
                            TurnInputQueueStatus.FAILED: QUEUE_EVENT_FAILED,
                        }[updated.status]
                        self._emit(event_type, updated.event_payload())
                    return
        raise TurnInputQueueError(
            "QUEUE_ENTRY_NOT_FOUND",
            "Turn input queue entry was not found.",
            {"queue_id": queue_id},
        )

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event_type, dict(payload))
        except Exception:
            return


def redact_text_preview(text: str, *, limit: int = 80) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def _coerce_intent(value: TurnInputIntent | str) -> TurnInputIntent:
    try:
        return (
            value if isinstance(value, TurnInputIntent) else TurnInputIntent(str(value))
        )
    except ValueError as exc:
        raise TurnInputQueueError(
            "INVALID_INTENT",
            f"Invalid turn input intent: {value}",
        ) from exc


def _coerce_status(value: TurnInputQueueStatus | str) -> TurnInputQueueStatus:
    try:
        return (
            value
            if isinstance(value, TurnInputQueueStatus)
            else TurnInputQueueStatus(str(value))
        )
    except ValueError as exc:
        raise TurnInputQueueError(
            "INVALID_STATUS",
            f"Invalid turn input status: {value}",
        ) from exc


def _idempotency_lookup_key(
    session_id: str,
    agent_id: str,
    idempotency_key: str | None,
) -> tuple[str, str, str] | None:
    normalized = str(idempotency_key or "").strip()
    if not normalized:
        return None
    return (session_id, agent_id, normalized)


def _require_non_empty(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise TurnInputQueueError(
            "INVALID_REQUEST",
            f"{field_name} must be non-empty.",
            {"field": field_name},
        )
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "QUEUE_EVENT_CANCEL_ACKNOWLEDGED",
    "QUEUE_EVENT_CANCEL_FAILED",
    "QUEUE_EVENT_CANCEL_REQUESTED",
    "QUEUE_EVENT_COMPLETED",
    "QUEUE_EVENT_DEQUEUED",
    "QUEUE_EVENT_DROPPED",
    "QUEUE_EVENT_ENQUEUED",
    "QUEUE_EVENT_FAILED",
    "QUEUE_EVENT_FULL",
    "QUEUE_EVENT_MOVED",
    "QUEUE_EVENT_STEER_DEFERRED",
    "TurnInputIntent",
    "TurnInputQueue",
    "TurnInputQueueEntry",
    "TurnInputQueueError",
    "TurnInputQueueStatus",
    "redact_text_preview",
]
