from __future__ import annotations

import unittest

from openminion.modules.memory.runtime.provenance import (
    MemoryProvenanceRecorder,
    default_provenance_recorder,
    set_default_provenance_recorder,
)
from openminion.services.agent.memory.context import ContextBuildersMixin


class _RecordingHarness(ContextBuildersMixin):
    def __init__(self) -> None:
        self._agent_id = "agent-test"


class TestRecordTurnProvenanceTrace(unittest.TestCase):
    def setUp(self) -> None:
        self._original_recorder = default_provenance_recorder()
        self._recorder = MemoryProvenanceRecorder()
        set_default_provenance_recorder(self._recorder)
        self._harness = _RecordingHarness()

    def tearDown(self) -> None:
        set_default_provenance_recorder(self._original_recorder)

    def _hit(
        self,
        *,
        record_id: str,
        source: str = "tool_output",
        created_at: str = "2026-05-18T00:00:00Z",
        score: float = 0.75,
        score_breakdown: dict[str, float] | None = None,
    ) -> dict[str, object]:
        return {
            "text": f"text for {record_id}",
            "score": score,
            "unified_score": score,
            "created_at": created_at,
            "meta": {
                "record_id": record_id,
                "record_source": source,
                "score_breakdown": score_breakdown or {},
            },
            "source_group": "memory",
        }

    def test_records_one_entry_per_hit_with_record_id(self) -> None:
        merged_hits = [
            self._hit(record_id="m1", source="tool_output", score=0.9),
            self._hit(record_id="m2", source="user_said", score=0.4),
        ]
        self._harness._record_turn_provenance_trace(
            session_id="s1",
            turn_id="t1",
            user_message="who is the user",
            merged_hits=merged_hits,
        )

        trace = self._recorder.get_turn_trace(session_id="s1", turn_id="t1")
        self.assertIsNotNone(trace)
        self.assertEqual(trace.query, "who is the user")
        self.assertEqual(len(trace.entries), 2)
        self.assertEqual(trace.entries[0].memory_id, "m1")
        self.assertEqual(trace.entries[0].source, "tool_output")
        self.assertEqual(trace.entries[0].retrieval_score, 0.9)
        self.assertEqual(trace.entries[1].memory_id, "m2")
        self.assertEqual(trace.entries[1].source, "user_said")

    def test_dedupes_repeat_record_ids(self) -> None:
        merged_hits = [
            self._hit(record_id="m1", score=0.9),
            self._hit(record_id="m1", score=0.5),
            self._hit(record_id="m2", score=0.3),
        ]
        self._harness._record_turn_provenance_trace(
            session_id="s1",
            turn_id="t1",
            user_message="q",
            merged_hits=merged_hits,
        )
        trace = self._recorder.get_turn_trace(session_id="s1", turn_id="t1")
        self.assertIsNotNone(trace)
        # m1 appears once (first occurrence wins); m2 appears once.
        ids = [entry.memory_id for entry in trace.entries]
        self.assertEqual(ids, ["m1", "m2"])
        # First-occurrence retrieval_score is preserved (0.9, not 0.5).
        self.assertEqual(trace.entries[0].retrieval_score, 0.9)

    def test_skips_hits_without_record_id(self) -> None:
        merged_hits = [
            {"meta": {"unit_id": "retrieve-ctl-unit-a"}, "score": 0.8},
            self._hit(record_id="m1", score=0.7),
        ]
        self._harness._record_turn_provenance_trace(
            session_id="s1",
            turn_id="t1",
            user_message="q",
            merged_hits=merged_hits,
        )
        trace = self._recorder.get_turn_trace(session_id="s1", turn_id="t1")
        self.assertIsNotNone(trace)
        # Only m1 lands; the retrieve-ctl hit without record_id is skipped.
        self.assertEqual([e.memory_id for e in trace.entries], ["m1"])

    def test_no_entries_means_no_trace_written(self) -> None:
        merged_hits = [
            {"meta": {"unit_id": "x"}, "score": 0.8},
            {"meta": {}, "score": 0.2},
        ]
        self._harness._record_turn_provenance_trace(
            session_id="s1",
            turn_id="t1",
            user_message="q",
            merged_hits=merged_hits,
        )
        # Recorder must NOT have an empty trace for (s1, t1).
        trace = self._recorder.get_turn_trace(session_id="s1", turn_id="t1")
        self.assertIsNone(trace)

    def test_score_breakdown_is_carried_through(self) -> None:
        merged_hits = [
            self._hit(
                record_id="m1",
                score=0.85,
                score_breakdown={
                    "relevance": 0.6,
                    "recency": 0.2,
                    "outcome_utility": 0.1,
                },
            ),
        ]
        self._harness._record_turn_provenance_trace(
            session_id="s1",
            turn_id="t1",
            user_message="q",
            merged_hits=merged_hits,
        )
        trace = self._recorder.get_turn_trace(session_id="s1", turn_id="t1")
        self.assertIsNotNone(trace)
        breakdown = trace.entries[0].score_breakdown
        self.assertEqual(breakdown["relevance"], 0.6)
        self.assertEqual(breakdown["recency"], 0.2)
        self.assertEqual(breakdown["outcome_utility"], 0.1)

    def test_falls_back_to_memory_source_when_record_source_missing(self) -> None:
        # Simulate a hit whose meta has record_id but no record_source
        merged_hits = [
            {
                "text": "x",
                "score": 0.5,
                "unified_score": 0.5,
                "created_at": "2026-05-18T00:00:00Z",
                "meta": {"record_id": "m-legacy"},
                "source_group": "memory",
            },
        ]
        self._harness._record_turn_provenance_trace(
            session_id="s1",
            turn_id="t1",
            user_message="q",
            merged_hits=merged_hits,
        )
        trace = self._recorder.get_turn_trace(session_id="s1", turn_id="t1")
        self.assertIsNotNone(trace)
        self.assertEqual(trace.entries[0].source, "memory")

    def test_record_id_index_lets_by_memory_lookup_find_the_trace(self) -> None:

        merged_hits = [self._hit(record_id="m1", score=0.9)]
        self._harness._record_turn_provenance_trace(
            session_id="s1",
            turn_id="t1",
            user_message="q",
            merged_hits=merged_hits,
        )
        traces = self._recorder.find_traces_citing_memory("m1")
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].session_id, "s1")
        self.assertEqual(traces[0].turn_id, "t1")
