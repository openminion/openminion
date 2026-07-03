from __future__ import annotations

import pytest

from openminion.modules.memory.contracts.provenance import (
    MemoryProvenanceEntry,
    TurnProvenanceTrace,
)
from openminion.modules.memory.runtime.provenance import (
    MemoryProvenanceRecorder,
    default_provenance_recorder,
    set_default_provenance_recorder,
)


def _entry(memory_id: str, score: float = 0.5) -> MemoryProvenanceEntry:
    return MemoryProvenanceEntry(
        memory_id=memory_id,
        source="tool_output",
        written_at="2026-05-18T00:00:00Z",
        retrieval_score=score,
    )


def _trace(
    session_id: str,
    turn_id: str,
    recorded_at: str,
    memory_ids: list[str],
) -> TurnProvenanceTrace:
    return TurnProvenanceTrace(
        session_id=session_id,
        turn_id=turn_id,
        recorded_at=recorded_at,
        entries=tuple(_entry(mid, score=0.7) for mid in memory_ids),
    )


@pytest.fixture
def recorder():
    return MemoryProvenanceRecorder()


class TestRecordAndGet:
    def test_record_then_get(self, recorder):
        trace = _trace("s1", "t1", "2026-05-18T00:00:00Z", ["m1", "m2"])
        recorder.record_turn_trace(trace)
        got = recorder.get_turn_trace(session_id="s1", turn_id="t1")
        assert got == trace

    def test_get_unknown_returns_none(self, recorder):
        assert recorder.get_turn_trace(session_id="x", turn_id="y") is None

    def test_overwrite_is_idempotent(self, recorder):
        first = _trace("s1", "t1", "2026-05-18T00:00:00Z", ["m1", "m2"])
        second = _trace("s1", "t1", "2026-05-18T00:01:00Z", ["m3"])
        recorder.record_turn_trace(first)
        recorder.record_turn_trace(second)
        got = recorder.get_turn_trace(session_id="s1", turn_id="t1")
        assert got is second  # newest wins


class TestFindTracesCitingMemory:
    def test_returns_traces_in_newest_first_order(self, recorder):
        t1 = _trace("s1", "t1", "2026-05-18T00:00:00Z", ["m1"])
        t2 = _trace("s1", "t2", "2026-05-18T00:05:00Z", ["m1", "m2"])
        t3 = _trace("s2", "t1", "2026-05-18T00:10:00Z", ["m1"])
        recorder.record_turn_trace(t1)
        recorder.record_turn_trace(t2)
        recorder.record_turn_trace(t3)
        traces = recorder.find_traces_citing_memory("m1")
        assert len(traces) == 3
        assert [t.recorded_at for t in traces] == [
            "2026-05-18T00:10:00Z",
            "2026-05-18T00:05:00Z",
            "2026-05-18T00:00:00Z",
        ]

    def test_memory_only_in_one_trace(self, recorder):
        t1 = _trace("s1", "t1", "2026-05-18T00:00:00Z", ["m1", "m2"])
        t2 = _trace("s1", "t2", "2026-05-18T00:05:00Z", ["m3"])
        recorder.record_turn_trace(t1)
        recorder.record_turn_trace(t2)
        m2_traces = recorder.find_traces_citing_memory("m2")
        assert len(m2_traces) == 1
        assert m2_traces[0].turn_id == "t1"

    def test_unknown_memory_returns_empty(self, recorder):
        assert recorder.find_traces_citing_memory("never-cited") == []

    def test_overwrite_updates_secondary_index(self, recorder):

        first = _trace("s1", "t1", "2026-05-18T00:00:00Z", ["m1", "m2"])
        second = _trace("s1", "t1", "2026-05-18T00:01:00Z", ["m3"])
        recorder.record_turn_trace(first)
        recorder.record_turn_trace(second)
        assert recorder.find_traces_citing_memory("m1") == []
        assert recorder.find_traces_citing_memory("m2") == []
        m3 = recorder.find_traces_citing_memory("m3")
        assert len(m3) == 1
        assert m3[0].turn_id == "t1"


class TestDefaultRecorderSingleton:
    def test_default_recorder_is_stable(self):
        a = default_provenance_recorder()
        b = default_provenance_recorder()
        assert a is b

    def test_set_default_recorder_replaces(self, recorder):
        original = default_provenance_recorder()
        try:
            set_default_provenance_recorder(recorder)
            assert default_provenance_recorder() is recorder
        finally:
            set_default_provenance_recorder(original)


class TestIterAllTraces:
    def test_snapshot_contains_recorded(self, recorder):
        recorder.record_turn_trace(_trace("s1", "t1", "2026-05-18T00:00:00Z", ["m1"]))
        recorder.record_turn_trace(_trace("s1", "t2", "2026-05-18T00:01:00Z", ["m2"]))
        all_traces = list(recorder.iter_all_traces())
        assert len(all_traces) == 2
