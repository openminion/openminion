from __future__ import annotations

from collections.abc import Callable
from queue import Empty, Full, Queue
from threading import Event, Thread, get_ident

from ..schemas import TelemetryEvent


_QUEUED_CRITICALITIES = frozenset({"noncritical", "trace", "diagnostic"})


class NoncriticalExportQueue:
    def __init__(
        self,
        *,
        capacity: int,
        flush_timeout_seconds: float,
        export_now: Callable[[TelemetryEvent], bool],
    ) -> None:
        self._capacity = max(0, int(capacity or 0))
        self._flush_timeout_seconds = max(0.0, float(flush_timeout_seconds or 0.0))
        self._export_now = export_now
        self._drops = 0
        self._flush_failures = 0
        self._stop = Event()
        self._worker_id: int | None = None
        self._queue: Queue[TelemetryEvent] | None = None
        self._worker: Thread | None = None
        if self._capacity > 0:
            self._queue = Queue(maxsize=self._capacity)
            self._worker = Thread(
                target=self._drain,
                name="openminion-otel-export-queue",
                daemon=True,
            )
            self._worker.start()

    def enabled(self) -> bool:
        return self._queue is not None

    def should_queue(self, event: TelemetryEvent) -> bool:
        if self._queue is None:
            return False
        if get_ident() == self._worker_id:
            return False
        event_type = str(event.event_type or "").strip()
        if event_type == "telemetry.queue.stats":
            return False
        payload = event.data if isinstance(event.data, dict) else {}
        criticality = str(payload.get("criticality", "") or "").strip().lower()
        return criticality in _QUEUED_CRITICALITIES

    def enqueue(self, event: TelemetryEvent) -> bool:
        if self._queue is None:
            return False
        try:
            self._queue.put_nowait(event)
        except Full:
            self._drops += 1
            return False
        return True

    def stats(self) -> dict[str, int]:
        depth = self._queue.qsize() if self._queue is not None else 0
        return {
            "queue_capacity": self._capacity,
            "queue_depth": int(depth),
            "drops": int(self._drops),
            "flush_failures": int(self._flush_failures),
        }

    def close(self) -> None:
        worker = self._worker
        if worker is None:
            return
        self._stop.set()
        worker.join(timeout=self._flush_timeout_seconds)
        if worker.is_alive():
            self._flush_failures += 1
        self._queue = None
        self._worker = None

    def _drain(self) -> None:
        self._worker_id = get_ident()
        queue_ref = self._queue
        if queue_ref is None:
            return
        while not self._stop.is_set() or not queue_ref.empty():
            try:
                event = queue_ref.get(timeout=0.05)
            except Empty:
                continue
            try:
                self._export_now(event)
            finally:
                queue_ref.task_done()


__all__ = ["NoncriticalExportQueue"]
