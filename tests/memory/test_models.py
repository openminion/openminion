from __future__ import annotations

import unittest

from openminion.modules.memory import models
from openminion.modules.memory.errors import InvalidArgumentError


class MemoryRecordTests(unittest.TestCase):
    def test_valid_record(self) -> None:
        record = models.MemoryRecord(
            id="rec-1",
            scope="session:test",
            type="fact",
            content={"text": "hello"},
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )
        self.assertEqual(record.scope, "session:test")

    def test_invalid_scope(self) -> None:
        with self.assertRaises(InvalidArgumentError):
            models.MemoryRecord(
                id="rec-2",
                scope="invalid",
                type="fact",
                content={"text": "oops"},
                created_at="2024-01-01T00:00:00Z",
                updated_at="2024-01-01T00:00:00Z",
            )

    def test_invalid_confidence(self) -> None:
        with self.assertRaises(InvalidArgumentError):
            models.MemoryRecord(
                id="rec-3",
                scope="agent:abc",
                type="fact",
                content={"text": "oops"},
                created_at="2024-01-01T00:00:00Z",
                updated_at="2024-01-01T00:00:00Z",
                confidence=2.0,
            )

    def test_new_memory_types_validate(self) -> None:
        for memory_type in (
            "user_preference",
            "project_convention",
            "correction",
            "tool_habit",
            "tool_outcome",
            "meta_rule_preference",
            "plan_snapshot",
        ):
            record = models.MemoryRecord(
                id=f"rec-{memory_type}",
                scope="agent:test",
                type=memory_type,  # type: ignore[arg-type]
                content={"text": memory_type},
                created_at="2024-01-01T00:00:00Z",
                updated_at="2024-01-01T00:00:00Z",
            )
            self.assertEqual(record.type, memory_type)

    def test_invalid_type_rejected(self) -> None:
        with self.assertRaises(InvalidArgumentError):
            models.MemoryRecord(
                id="rec-invalid-type",
                scope="agent:test",
                type="made_up_type",  # type: ignore[arg-type]
                content={"text": "oops"},
                created_at="2024-01-01T00:00:00Z",
                updated_at="2024-01-01T00:00:00Z",
            )


class MemoryScopeTests(unittest.TestCase):
    def test_parse_session_scope(self) -> None:
        scope = models.MemoryScope.parse("session:test")
        self.assertTrue(scope.is_session)
        self.assertEqual(scope.kind, "session")
        self.assertEqual(scope.value, "test")
        self.assertEqual(str(scope), "session:test")

    def test_coerce_plain_scope_defaults_to_session(self) -> None:
        scope = models.MemoryScope.coerce("sess-1")
        self.assertTrue(scope.is_session)
        self.assertEqual(str(scope), "session:sess-1")

    def test_parse_global_scope_flags_kind(self) -> None:
        scope = models.MemoryScope.parse("global:all")
        self.assertTrue(scope.is_global)
        self.assertEqual(scope.value, "all")

    def test_coerce_empty_scope_raises(self) -> None:
        with self.assertRaises(InvalidArgumentError):
            models.MemoryScope.coerce("")


class MemoryCandidateTests(unittest.TestCase):
    def test_plan_snapshot_candidate_validates(self) -> None:
        candidate = models.MemoryCandidate(
            candidate_id="cand-plan",
            session_id="sess-1",
            proposed_scope="agent:test",
            type="plan_snapshot",
            content={
                "plan_steps": [{"step_id": "cmd-1", "status": "in_progress"}],
                "intent_states": [{"intent_id": "intent-1", "status": "pending"}],
                "last_work_summary": "Continue the migration.",
                "incomplete_reason": "session_ended",
                "session_id": "sess-1",
                "turn_index": 2,
                "text": '{"plan_steps": [{"step_id": "cmd-1", "status": "in_progress"}]}',
            },
        )
        self.assertEqual(candidate.type, "plan_snapshot")

    def test_invalid_status(self) -> None:
        with self.assertRaises(InvalidArgumentError):
            models.MemoryCandidate(
                candidate_id="cand-1",
                session_id="sess-1",
                proposed_scope="session:sess-1",
                type="fact",
                content="content",
                status="unknown",  # type: ignore[arg-type]
            )

    def test_invalid_scope(self) -> None:
        with self.assertRaises(InvalidArgumentError):
            models.MemoryCandidate(
                candidate_id="cand-2",
                session_id="sess-1",
                proposed_scope="bad",
                type="fact",
                content="content",
            )
