import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock
from openminion.modules.memory.contracts.types import MemoryProcedure
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.models import MemoryCandidate, MemoryRecord
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.errors import (
    InvalidArgumentError,
    NotFoundError,
    PromotionDeniedError,
)
from openminion.modules.memory.runtime.promotion import PromotionPolicy


class TestMemoryService(unittest.TestCase):
    def setUp(self):
        self.store = MagicMock()
        self.policy = PromotionPolicy(auto_promote_sources={"validated"})
        self.service = MemoryService(self.store, self.policy)

    def test_get_not_found(self):
        self.store.get.return_value = None
        with self.assertRaises(NotFoundError):
            self.service.get("missing")

    def test_candidate_get_not_found(self):
        self.store.candidate_get.return_value = None
        with self.assertRaises(NotFoundError):
            self.service.candidate_get("missing")

    def test_promote_denied(self):
        candidate = MemoryCandidate(
            candidate_id="c1",
            session_id="s1",
            proposed_scope="global:all",
            type="fact",
            content="test",
            source="agent_inferred",
            status="proposed",
        )
        self.store.candidate_get.return_value = candidate

        with self.assertRaises(PromotionDeniedError):
            self.service.promote_candidate("c1", "global:all")

    def test_promote_allowed(self):
        candidate = MemoryCandidate(
            candidate_id="c1",
            session_id="s1",
            proposed_scope="global:all",
            type="fact",
            content="test",
            source="validated",
            status="approved",
        )
        self.store.candidate_get.return_value = candidate
        self.store.promote_candidate.return_value = "mock_record"

        record = self.service.promote_candidate("c1", "global:all")
        self.assertEqual(record, "mock_record")
        self.store.promote_candidate.assert_called_once_with("c1", "global:all")

    def test_invalidate_requires_reason(self):
        with self.assertRaises(InvalidArgumentError):
            self.service.invalidate("mem_1", reason="   ")

    def test_invalidate_delegates_to_store(self):
        now = datetime.now(timezone.utc).isoformat()
        record = MemoryRecord(
            id="mem_1",
            scope="session:s1",
            type="fact",
            content={"text": "alpha"},
            created_at=now,
            updated_at=now,
            valid_to=now,
        )
        self.store.invalidate.return_value = record

        result = self.service.invalidate(
            "mem_1",
            valid_to="2026-05-21T00:00:00+00:00",
            reason="corrected",
        )

        self.assertEqual(result, record)
        self.store.invalidate.assert_called_once_with(
            "mem_1",
            valid_to="2026-05-21T00:00:00+00:00",
            reason="corrected",
        )

    def _procedure_record(
        self,
        *,
        record_id: str = "proc-1",
        title: str = "deploy database migration",
        content: dict | str | None = None,
    ) -> MemoryRecord:
        now = datetime.now(timezone.utc).isoformat()
        if content is None:
            content = {
                "steps": ["take a backup", "run migration", "verify checksum"],
                "preflight": ["confirm staging is green"],
                "rollback_hint": "restore from backup",
            }
        return MemoryRecord(
            id=record_id,
            scope="agent:openminion",
            type="procedure",
            content=content,
            created_at=now,
            updated_at=now,
            title=title,
        )

    def test_get_procedure_returns_typed_memory_procedure(self):
        record = self._procedure_record()
        self.store.get.return_value = record

        payload = self.service.get_procedure(procedure_id="proc-1")

        self.assertIsInstance(payload, MemoryProcedure)
        self.assertEqual(payload.procedure_id, "proc-1")
        self.assertEqual(payload.title, "deploy database migration")
        self.assertEqual(
            payload.steps,
            ["take a backup", "run migration", "verify checksum"],
        )
        self.assertEqual(payload.preflight, ["confirm staging is green"])
        self.assertEqual(payload.rollback_hint, "restore from backup")

    def test_get_procedure_returns_none_when_record_missing(self):
        self.store.get.return_value = None
        self.assertIsNone(self.service.get_procedure(procedure_id="missing"))

    def test_get_procedure_returns_none_for_non_procedure_type(self):
        now = datetime.now(timezone.utc).isoformat()
        non_procedure = MemoryRecord(
            id="proc-1",
            scope="agent:openminion",
            type="fact",
            content="not a procedure",
            created_at=now,
            updated_at=now,
        )
        self.store.get.return_value = non_procedure
        self.assertIsNone(self.service.get_procedure(procedure_id="proc-1"))

    def test_get_procedure_handles_string_content(self):
        record = self._procedure_record(content="single-step body text")
        self.store.get.return_value = record
        payload = self.service.get_procedure(procedure_id="proc-1")
        self.assertIsInstance(payload, MemoryProcedure)
        self.assertEqual(payload.steps, ["single-step body text"])
        self.assertEqual(payload.preflight, [])
        self.assertEqual(payload.rollback_hint, "")

    def test_get_procedure_returns_none_for_blank_id(self):
        self.assertIsNone(self.service.get_procedure(procedure_id=""))
        self.assertIsNone(self.service.get_procedure(procedure_id="   "))

    def test_get_procedure_no_longer_returns_unsupported_contract(self):
        self.store.get.return_value = None
        result_missing = self.service.get_procedure(procedure_id="proc-1")
        self.assertIsNone(result_missing)
        self.assertNotIsInstance(result_missing, dict)

        self.store.get.side_effect = RuntimeError("backend unavailable")
        result_error = self.service.get_procedure(procedure_id="proc-1")
        self.assertIsNone(result_error)
        self.assertNotIsInstance(result_error, dict)

    def test_search_semantic_blends_bm25_meta_with_vector_scores(self):
        now = datetime.now(timezone.utc).isoformat()
        rec_a = MemoryRecord(
            id="a",
            scope="session:s1",
            type="fact",
            content="alpha lexical dominant",
            created_at=now,
            updated_at=now,
            meta={"bm25_score": 0.9},
        )
        rec_b = MemoryRecord(
            id="b",
            scope="session:s1",
            type="fact",
            content="beta vector dominant",
            created_at=now,
            updated_at=now,
            meta={"bm25_score": 0.1},
        )
        self.store.search.return_value = [rec_a, rec_b]
        self.store.get.side_effect = lambda rid: {"a": rec_a, "b": rec_b}.get(rid)
        vector = MagicMock()
        vector.search.return_value = [
            ("a", 0.2, {}),
            ("b", 0.8, {}),
        ]
        self.service.set_vector_adapter(vector)

        results = self.service.search_semantic(
            query="dominant",
            scopes=["session:s1"],
            limit=2,
        )

        self.assertEqual([item.id for item in results], ["a", "b"])

    def test_search_semantic_skips_superseded_vector_only_hits(self):
        now = datetime.now(timezone.utc).isoformat()
        live = MemoryRecord(
            id="live",
            scope="agent:a1",
            type="fact",
            content="new email is b@y.com",
            created_at=now,
            updated_at=now,
            meta={"bm25_score": 0.9},
        )
        superseded = MemoryRecord(
            id="old",
            scope="agent:a1",
            type="fact",
            content="old email is a@x.com",
            created_at=now,
            updated_at=now,
            superseded_by_id="live",
            is_deleted=True,
        )
        self.store.search.return_value = [live]
        self.store.get.side_effect = lambda rid: {
            "live": live,
            "old": superseded,
        }.get(rid)
        vector = MagicMock()
        vector.search.return_value = [
            ("old", 0.99, {}),
            ("live", 0.4, {}),
        ]
        self.service.set_vector_adapter(vector)

        results = self.service.search_semantic(
            query="what is my email",
            scopes=["agent:a1"],
            limit=2,
        )

        self.assertEqual([item.id for item in results], ["live"])

    def test_apply_outcome_feedback_dedupes_ids_and_delegates(self):
        self.store.apply_outcome_feedback.return_value = 1

        updated = self.service.apply_outcome_feedback(
            record_ids=["mem_1", "mem_1", "mem_2", ""],
            outcome="success",
            command_id="cmd-1",
            observed_at="2026-03-28T00:00:00+00:00",
            feedback_delta=0.2,
        )

        self.assertEqual(updated, 1)
        self.store.apply_outcome_feedback.assert_called_once_with(
            ["mem_1", "mem_2"],
            outcome="success",
            command_id="cmd-1",
            observed_at="2026-03-28T00:00:00+00:00",
            feedback_delta=0.2,
        )

    def test_apply_outcome_feedback_rejects_invalid_outcome(self):
        with self.assertRaises(InvalidArgumentError):
            self.service.apply_outcome_feedback(
                record_ids=["mem_1"],
                outcome="blocked",  # type: ignore[arg-type]
                command_id="cmd-1",
                observed_at="2026-03-28T00:00:00+00:00",
                feedback_delta=0.0,
            )

    def test_transition_tier_delegates_to_store(self):
        self.store.transition_tier.return_value = "transition"

        result = self.service.transition_tier(
            record_id="mem_1",
            to_tier="archival",
            transition_reason="manual_override",
            transition_at="2026-05-10T00:00:00+00:00",
        )

        self.assertEqual(result, "transition")
        self.store.transition_tier.assert_called_once_with(
            "mem_1",
            to_tier="archival",
            transition_reason="manual_override",
            transition_at="2026-05-10T00:00:00+00:00",
            meta=None,
        )

    def test_reconcile_tiers_promotes_and_reactivates_based_on_typed_facts(self):
        store = InMemoryMemoryStore()
        service = MemoryService(store)
        service.set_tiering_config(
            type(
                "TierCfg",
                (),
                {
                    "enabled": True,
                    "promotion_age_days": 30,
                    "reaccess_promote_threshold": 3,
                    "max_working_access_count": 1,
                },
            )()
        )

        old_record = MemoryRecord(
            id="old",
            scope="session:s1",
            type="fact",
            content="old memory",
            created_at="2026-03-01T00:00:00+00:00",
            updated_at="2026-03-01T00:00:00+00:00",
            access_count=0,
        )
        archived_record = MemoryRecord(
            id="arch",
            scope="session:s1",
            type="fact",
            content="archived memory",
            created_at="2026-03-01T00:00:00+00:00",
            updated_at="2026-03-01T00:00:00+00:00",
            tier="archival",
            access_count=3,
        )
        store.put(old_record)
        store.put(archived_record)

        transitions = service.reconcile_tiers(scopes=["session:s1"])

        self.assertEqual(len(transitions), 2)
        refreshed_old = store.get("old")
        refreshed_arch = store.get("arch")
        self.assertIsNotNone(refreshed_old)
        self.assertIsNotNone(refreshed_arch)
        self.assertEqual(refreshed_old.tier, "archival")
        self.assertEqual(refreshed_arch.tier, "working")
