from __future__ import annotations

import unittest
from typing import Any

from openminion.modules.memory.runtime.staging import (
    AFE_INITIAL_CONFIDENCE,
    AFE_SOURCE_TAG,
    ExtractedCandidateDTO,
    stage_extracted_candidates,
)


def _dto(
    *,
    kind: str,
    normalized_key: str,
    title: str,
    content: Any,
    model_confidence: float | None = None,
) -> ExtractedCandidateDTO:
    return ExtractedCandidateDTO(
        kind=kind,
        normalized_key=normalized_key,
        title=title,
        content=content,
        model_confidence=model_confidence,
    )


def _stage(
    memory_service: Any,
    *,
    candidates: list[ExtractedCandidateDTO],
    trace_id: str | None = None,
    initial_confidence: float | None = None,
    scope_override: str | None = None,
) -> Any:
    kwargs: dict[str, Any] = {}
    if initial_confidence is not None:
        kwargs["initial_confidence"] = initial_confidence
    if scope_override is not None:
        kwargs["scope_override"] = scope_override
    return stage_extracted_candidates(
        memory_service=memory_service,
        session_id="s1",
        agent_id="agent-x",
        trace_id=trace_id,
        candidates=candidates,
        **kwargs,
    )


class _MockMemoryService:
    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raise = raise_exc

    def stage_candidate(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: Any,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        confidence: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        if self._raise is not None:
            raise self._raise
        self.calls.append(
            {
                "scope": scope,
                "record_type": record_type,
                "title": title,
                "content": content,
                "tags": list(tags or []),
                "confidence": confidence,
                "meta": dict(meta or {}),
            }
        )
        return f"cand_{len(self.calls)}"


class StageExtractedCandidatesTests(unittest.TestCase):
    def test_stages_typed_dto_with_afe_provenance(self) -> None:
        memory = _MockMemoryService()
        result = _stage(
            memory,
            trace_id="trace-1",
            candidates=[
                _dto(
                    kind="fact",
                    normalized_key="fact:user_name",
                    title="user name",
                    content="Jay",
                ),
            ],
        )

        self.assertEqual(result.staged_count, 1)
        self.assertEqual(len(result.candidate_ids), 1)
        self.assertEqual(len(memory.calls), 1)
        call = memory.calls[0]
        self.assertEqual(call["record_type"], "fact")
        self.assertEqual(call["scope"], "agent:agent-x")
        self.assertEqual(call["confidence"], AFE_INITIAL_CONFIDENCE)
        self.assertEqual(call["meta"]["source"], AFE_SOURCE_TAG)
        self.assertEqual(call["meta"]["source_agent_id"], "agent-x")
        self.assertEqual(call["meta"]["source_session_id"], "s1")
        self.assertEqual(call["meta"]["source_trace_id"], "trace-1")
        self.assertEqual(call["meta"]["normalized_key"], "fact:user_name")

    def test_maps_kind_to_record_type(self) -> None:
        memory = _MockMemoryService()
        _stage(
            memory,
            trace_id=None,
            candidates=[
                _dto(
                    kind="fact",
                    normalized_key="fact:x",
                    title="a",
                    content="b",
                ),
                _dto(
                    kind="user_preference",
                    normalized_key="user_preference:y",
                    title="c",
                    content="d",
                ),
                _dto(
                    kind="task",
                    normalized_key="task:z",
                    title="e",
                    content="f",
                ),
            ],
        )
        record_types = [call["record_type"] for call in memory.calls]
        self.assertEqual(record_types, ["fact", "user_preference", "task"])

    def test_rejects_unsupported_kind_without_staging(self) -> None:
        memory = _MockMemoryService()
        result = _stage(
            memory,
            trace_id=None,
            candidates=[
                _dto(
                    kind="unknown",
                    normalized_key="unknown:z",
                    title="t",
                    content="c",
                ),
            ],
        )
        self.assertEqual(result.staged_count, 0)
        self.assertEqual(memory.calls, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.skipped[0]["reason"], "unsupported_kind")

    def test_skips_empty_title_or_content(self) -> None:
        memory = _MockMemoryService()
        result = _stage(
            memory,
            trace_id=None,
            candidates=[
                _dto(
                    kind="fact",
                    normalized_key="fact:x",
                    title="",
                    content="something",
                ),
                _dto(
                    kind="fact",
                    normalized_key="fact:y",
                    title="has title",
                    content="",
                ),
            ],
        )
        self.assertEqual(result.staged_count, 0)
        self.assertEqual(len(result.skipped), 2)
        self.assertTrue(
            all(s["reason"] == "empty_title_or_content" for s in result.skipped)
        )

    def test_rebuilds_invalid_normalized_key(self) -> None:
        memory = _MockMemoryService()
        _stage(
            memory,
            trace_id=None,
            candidates=[
                _dto(
                    kind="fact",
                    normalized_key="NOT A KEY",
                    title="user name",
                    content="Jay",
                ),
            ],
        )
        self.assertEqual(len(memory.calls), 1)
        key = memory.calls[0]["meta"]["normalized_key"]
        self.assertTrue(key.startswith("fact:"))
        self.assertNotIn(" ", key)
        self.assertNotIn("NOT", key)

    def test_scope_override_wins(self) -> None:
        memory = _MockMemoryService()
        _stage(
            memory,
            trace_id=None,
            candidates=[
                _dto(
                    kind="fact",
                    normalized_key="fact:x",
                    title="t",
                    content="c",
                ),
            ],
            scope_override="session:s1",
        )
        self.assertEqual(memory.calls[0]["scope"], "session:s1")

    def test_model_confidence_recorded_in_meta_only(self) -> None:
        memory = _MockMemoryService()
        _stage(
            memory,
            trace_id=None,
            candidates=[
                _dto(
                    kind="fact",
                    normalized_key="fact:x",
                    title="t",
                    content="c",
                    model_confidence=0.95,
                ),
            ],
        )
        call = memory.calls[0]
        self.assertEqual(call["confidence"], AFE_INITIAL_CONFIDENCE)
        self.assertEqual(call["meta"]["model_declared_confidence"], 0.95)

    def test_initial_confidence_override_applied(self) -> None:
        memory = _MockMemoryService()
        _stage(
            memory,
            trace_id=None,
            candidates=[
                _dto(
                    kind="fact",
                    normalized_key="fact:x",
                    title="t",
                    content="c",
                ),
            ],
            initial_confidence=0.55,
        )
        self.assertEqual(memory.calls[0]["confidence"], 0.55)

    def test_initial_confidence_out_of_range_falls_back_to_default(self) -> None:
        memory = _MockMemoryService()
        _stage(
            memory,
            trace_id=None,
            candidates=[
                _dto(
                    kind="fact",
                    normalized_key="fact:x",
                    title="t",
                    content="c",
                ),
            ],
            initial_confidence=1.5,  # invalid — out of [0,1]
        )
        self.assertEqual(memory.calls[0]["confidence"], AFE_INITIAL_CONFIDENCE)

    def test_empty_candidates_returns_no_work(self) -> None:
        memory = _MockMemoryService()
        result = _stage(
            memory,
            trace_id=None,
            candidates=[],
        )
        self.assertEqual(result.staged_count, 0)
        self.assertEqual(result.candidate_ids, ())
        self.assertEqual(result.skipped, ())
        self.assertEqual(memory.calls, [])

    def test_missing_stage_candidate_method_returns_unsupported(self) -> None:
        class _NoStage:
            pass

        result = _stage(
            _NoStage(),
            trace_id=None,
            candidates=[
                _dto(
                    kind="fact",
                    normalized_key="fact:x",
                    title="t",
                    content="c",
                ),
            ],
        )
        self.assertEqual(result.staged_count, 0)
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.skipped[0]["reason"], "memory_service_unsupported")

    def test_stage_candidate_failure_captured_in_skipped(self) -> None:
        memory = _MockMemoryService(raise_exc=RuntimeError("store down"))
        result = _stage(
            memory,
            trace_id=None,
            candidates=[
                _dto(
                    kind="fact",
                    normalized_key="fact:x",
                    title="t",
                    content="c",
                ),
            ],
        )
        self.assertEqual(result.staged_count, 0)
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.skipped[0]["reason"], "stage_candidate_failed")
        self.assertIn("store down", result.skipped[0]["error"])


class _ReinforcingMockMemoryService:
    def __init__(self) -> None:
        self.stage_calls: list[dict[str, Any]] = []
        self.reinforce_calls: list[str] = []
        self._by_key: dict[tuple[str, str], str] = {}
        self.reconfirmation: dict[str, int] = {}

    def stage_candidate(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: Any,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        confidence: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        candidate_id = f"cand_{len(self.stage_calls) + 1}"
        self.stage_calls.append(
            {
                "scope": scope,
                "record_type": record_type,
                "title": title,
                "meta": dict(meta or {}),
                "candidate_id": candidate_id,
            }
        )
        normalized_key = str((meta or {}).get("normalized_key") or "")
        if normalized_key:
            self._by_key[(scope, normalized_key)] = candidate_id
        self.reconfirmation[candidate_id] = 0
        return candidate_id

    def find_candidate_by_normalized_key(
        self, *, scope: str, normalized_key: str
    ) -> str | None:
        return self._by_key.get((scope, normalized_key))

    def reinforce_candidate(self, *, candidate_id: str) -> None:
        self.reinforce_calls.append(candidate_id)
        self.reconfirmation[candidate_id] = self.reconfirmation.get(candidate_id, 0) + 1


class ReinforcementByKeyTests(unittest.TestCase):
    def test_repeated_same_key_reinforces_existing_candidate(self) -> None:
        memory = _ReinforcingMockMemoryService()

        dto = _dto(
            kind="fact",
            normalized_key="fact:user_name",
            title="user name",
            content="Jay",
        )

        r1 = _stage(memory, trace_id="trace-1", candidates=[dto])
        r2 = _stage(memory, trace_id="trace-2", candidates=[dto])
        r3 = _stage(memory, trace_id="trace-3", candidates=[dto])

        self.assertEqual(len(memory.stage_calls), 1)
        self.assertEqual(len(memory.reinforce_calls), 2)
        original_id = memory.stage_calls[0]["candidate_id"]
        self.assertEqual(memory.reconfirmation[original_id], 2)
        self.assertEqual(r1.candidate_ids, (original_id,))
        self.assertEqual(r2.candidate_ids, (original_id,))
        self.assertEqual(r3.candidate_ids, (original_id,))
        self.assertEqual(r1.staged_count, 1)
        self.assertEqual(r2.staged_count, 1)
        self.assertEqual(r3.staged_count, 1)

    def test_distinct_keys_do_not_reinforce_each_other(self) -> None:
        memory = _ReinforcingMockMemoryService()

        _stage(
            memory,
            trace_id=None,
            candidates=[
                _dto(
                    kind="fact",
                    normalized_key="fact:user_name",
                    title="user name",
                    content="Jay",
                ),
                _dto(
                    kind="user_preference",
                    normalized_key="user_preference:language",
                    title="preferred language",
                    content="TypeScript",
                ),
            ],
        )
        self.assertEqual(len(memory.stage_calls), 2)
        self.assertEqual(len(memory.reinforce_calls), 0)

    def test_without_reinforcement_surface_falls_back_to_stage(self) -> None:
        memory = _MockMemoryService()
        dto = _dto(
            kind="fact",
            normalized_key="fact:user_name",
            title="user name",
            content="Jay",
        )
        for _ in range(3):
            _stage(memory, trace_id=None, candidates=[dto])
        self.assertEqual(len(memory.calls), 3)
