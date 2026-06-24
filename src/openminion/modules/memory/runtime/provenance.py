"""In-memory recorder for per-turn memory provenance traces."""

import threading
from typing import Iterable

from openminion.modules.memory.contracts.provenance import (
    MemoryProvenanceEntry,
    TurnProvenanceTrace,
)


class MemoryProvenanceRecorder:
    """Thread-safe in-process recorder for ``TurnProvenanceTrace``."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._traces: dict[tuple[str, str], TurnProvenanceTrace] = {}
        self._memory_index: dict[str, set[tuple[str, str]]] = {}

    def record_turn_trace(self, trace: TurnProvenanceTrace) -> None:
        """Persist a single per-turn trace, overwriting prior retries."""

        key = (trace.session_id, trace.turn_id)
        with self._lock:
            old = self._traces.get(key)
            if old is not None:
                for entry in old.entries:
                    bucket = self._memory_index.get(entry.memory_id)
                    if bucket is not None:
                        bucket.discard(key)
                        if not bucket:
                            self._memory_index.pop(entry.memory_id, None)
            self._traces[key] = trace
            for entry in trace.entries:
                self._memory_index.setdefault(entry.memory_id, set()).add(key)

    def get_turn_trace(
        self,
        *,
        session_id: str,
        turn_id: str,
    ) -> TurnProvenanceTrace | None:
        """Return the trace for ``(session_id, turn_id)`` or None."""

        with self._lock:
            return self._traces.get((session_id, turn_id))

    def find_traces_citing_memory(
        self,
        memory_id: str,
    ) -> list[TurnProvenanceTrace]:
        """Return every trace that cited ``memory_id``, newest-first."""

        with self._lock:
            keys = list(self._memory_index.get(memory_id, ()))
            if not keys:
                return []
            traces = [self._traces[k] for k in keys if k in self._traces]
        return sorted(
            traces,
            key=lambda t: (t.recorded_at, t.session_id, t.turn_id),
            reverse=True,
        )

    def iter_all_traces(self) -> Iterable[TurnProvenanceTrace]:
        """Return a snapshot iterator over every recorded trace."""

        with self._lock:
            return list(self._traces.values())

    def clear(self) -> None:
        """Drop every recorded trace + index. Used by tests."""

        with self._lock:
            self._traces.clear()
            self._memory_index.clear()


_DEFAULT_RECORDER: MemoryProvenanceRecorder | None = None
_DEFAULT_LOCK = threading.Lock()


def default_provenance_recorder() -> MemoryProvenanceRecorder:
    """Process-wide singleton recorder for runtime use."""

    global _DEFAULT_RECORDER
    with _DEFAULT_LOCK:
        if _DEFAULT_RECORDER is None:
            _DEFAULT_RECORDER = MemoryProvenanceRecorder()
        return _DEFAULT_RECORDER


def set_default_provenance_recorder(recorder: MemoryProvenanceRecorder) -> None:
    """Replace the process-wide recorder. Used by tests for isolation."""

    global _DEFAULT_RECORDER
    with _DEFAULT_LOCK:
        _DEFAULT_RECORDER = recorder


__all__ = [
    "MemoryProvenanceRecorder",
    "default_provenance_recorder",
    "set_default_provenance_recorder",
    "MemoryProvenanceEntry",
    "TurnProvenanceTrace",
]
