from __future__ import annotations

import unittest

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.memory import InMemoryMemoryStore


class SupersededPropertyTests(unittest.TestCase):
    def test_live_record_reports_not_superseded(self) -> None:
        r = MemoryRecord(
            id="a",
            scope="session:s1",
            type="fact",
            content="user email is a@x.com",
            created_at="t",
            updated_at="t",
        )
        self.assertFalse(r.superseded)

    def test_record_with_superseded_by_id_reports_superseded(self) -> None:
        r = MemoryRecord(
            id="b",
            scope="session:s1",
            type="fact",
            content="user email is a@x.com",
            created_at="t",
            updated_at="t",
            superseded_by_id="c",
        )
        self.assertTrue(r.superseded)


class StageCandidateNormalizedKeyCopyTests(unittest.TestCase):
    def test_normalized_key_in_meta_is_copied_to_candidate_key(self) -> None:
        service = MemoryService(store=InMemoryMemoryStore())
        cid = service.stage_candidate(
            scope="agent:a1",
            record_type="fact",
            title="user email",
            content="a@x.com",
            meta={"normalized_key": "fact:user_email"},
        )
        candidate = service.candidate_get(cid)
        self.assertEqual(candidate.key, "fact:user_email")
        # meta is preserved (AFE still reads from meta in places).
        self.assertEqual(candidate.meta.get("normalized_key"), "fact:user_email")

    def test_missing_normalized_key_leaves_candidate_key_none(self) -> None:
        service = MemoryService(store=InMemoryMemoryStore())
        cid = service.stage_candidate(
            scope="agent:a1",
            record_type="fact",
            title="unkeyed",
            content="something",
        )
        candidate = service.candidate_get(cid)
        self.assertIsNone(candidate.key)

    def test_whitespace_only_normalized_key_is_treated_as_none(self) -> None:
        service = MemoryService(store=InMemoryMemoryStore())
        cid = service.stage_candidate(
            scope="agent:a1",
            record_type="fact",
            title="bad key",
            content="something",
            meta={"normalized_key": "   "},
        )
        candidate = service.candidate_get(cid)
        self.assertIsNone(candidate.key)


class FindRecordByNormalizedKeyTests(unittest.TestCase):
    def _seed_record(
        self,
        service: MemoryService,
        *,
        scope: str,
        record_type: str,
        key: str,
        content: str,
        record_id: str = "r1",
    ) -> MemoryRecord:
        record = MemoryRecord(
            id=record_id,
            scope=scope,
            type=record_type,
            key=key,
            content=content,
            created_at="t",
            updated_at="t",
        )
        # Reach through the capability wrapper to the
        # InMemoryRecordStore's internal dict for deterministic id seeding.
        service._store._records._records[record_id] = record
        return record

    def test_returns_live_record_matching_key(self) -> None:
        service = MemoryService(store=InMemoryMemoryStore())
        self._seed_record(
            service,
            scope="agent:a1",
            record_type="fact",
            key="fact:user_email",
            content="a@x.com",
        )
        hit = service.find_record_by_normalized_key(
            scope="agent:a1",
            record_type="fact",
            normalized_key="fact:user_email",
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit.id, "r1")

    def test_returns_none_when_no_match(self) -> None:
        service = MemoryService(store=InMemoryMemoryStore())
        hit = service.find_record_by_normalized_key(
            scope="agent:a1",
            record_type="fact",
            normalized_key="fact:user_email",
        )
        self.assertIsNone(hit)

    def test_returns_none_for_blank_inputs(self) -> None:
        service = MemoryService(store=InMemoryMemoryStore())
        self.assertIsNone(
            service.find_record_by_normalized_key(
                scope="", record_type="fact", normalized_key="fact:x"
            )
        )
        self.assertIsNone(
            service.find_record_by_normalized_key(
                scope="agent:a1", record_type="", normalized_key="fact:x"
            )
        )
        self.assertIsNone(
            service.find_record_by_normalized_key(
                scope="agent:a1", record_type="fact", normalized_key=""
            )
        )

    def test_excludes_superseded_records(self) -> None:
        service = MemoryService(store=InMemoryMemoryStore())
        old = self._seed_record(
            service,
            scope="agent:a1",
            record_type="fact",
            key="fact:user_email",
            content="a@x.com",
            record_id="old",
        )
        new = self._seed_record(
            service,
            scope="agent:a1",
            record_type="fact",
            key="fact:user_email",
            content="b@y.com",
            record_id="new",
        )
        service.supersede_by_contradiction(old.id, new.id, reason="test")

        hit = service.find_record_by_normalized_key(
            scope="agent:a1",
            record_type="fact",
            normalized_key="fact:user_email",
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit.id, "new")
        self.assertTrue(service._store._records._records[old.id].superseded)

    def test_scope_isolated(self) -> None:
        service = MemoryService(store=InMemoryMemoryStore())
        self._seed_record(
            service,
            scope="agent:a1",
            record_type="fact",
            key="fact:user_email",
            content="agent-value",
            record_id="a1-rec",
        )
        self._seed_record(
            service,
            scope="session:s1",
            record_type="fact",
            key="fact:user_email",
            content="session-value",
            record_id="s1-rec",
        )
        agent_hit = service.find_record_by_normalized_key(
            scope="agent:a1",
            record_type="fact",
            normalized_key="fact:user_email",
        )
        session_hit = service.find_record_by_normalized_key(
            scope="session:s1",
            record_type="fact",
            normalized_key="fact:user_email",
        )
        self.assertEqual(agent_hit.id, "a1-rec")
        self.assertEqual(session_hit.id, "s1-rec")


class ReinforceRecordTests(unittest.TestCase):
    def test_reinforce_boosts_confidence(self) -> None:
        service = MemoryService(store=InMemoryMemoryStore())
        record = MemoryRecord(
            id="r1",
            scope="agent:a1",
            type="fact",
            key="fact:user_email",
            content="a@x.com",
            confidence=0.5,
            created_at="t",
            updated_at="t",
        )
        service._store._records._records["r1"] = record

        updated = service.reinforce_record(record_id="r1")
        self.assertAlmostEqual(updated.confidence, 0.6, places=6)

    def test_reinforce_clamps_at_confidence_max(self) -> None:
        service = MemoryService(store=InMemoryMemoryStore())
        record = MemoryRecord(
            id="r1",
            scope="agent:a1",
            type="fact",
            key="fact:user_email",
            content="a@x.com",
            confidence=0.85,
            created_at="t",
            updated_at="t",
        )
        service._store._records._records["r1"] = record

        updated = service.reinforce_record(record_id="r1")
        self.assertAlmostEqual(updated.confidence, 0.9, places=6)
        again = service.reinforce_record(record_id="r1")
        self.assertAlmostEqual(again.confidence, 0.9, places=6)

    def test_reinforce_with_tuned_config(self) -> None:
        from openminion.modules.memory.config import CandidateLearningConfig

        service = MemoryService(store=InMemoryMemoryStore())
        service.set_candidate_learning_config(
            CandidateLearningConfig(
                confidence_boost_per_reconfirmation=0.25,
                confidence_max=0.8,
            )
        )
        record = MemoryRecord(
            id="r1",
            scope="agent:a1",
            type="fact",
            key="fact:user_email",
            content="a@x.com",
            confidence=0.3,
            created_at="t",
            updated_at="t",
        )
        service._store._records._records["r1"] = record
        updated = service.reinforce_record(record_id="r1")
        self.assertAlmostEqual(updated.confidence, 0.55, places=6)

    def test_reinforce_missing_record_raises(self) -> None:
        from openminion.modules.memory.errors import NotFoundError

        service = MemoryService(store=InMemoryMemoryStore())
        with self.assertRaises(NotFoundError):
            service.reinforce_record(record_id="missing")


class NormalizedValueEqualTests(unittest.TestCase):
    def test_exact_match(self) -> None:
        from openminion.services.agent.memory.learning import (
            _normalized_value_equal,
        )

        self.assertTrue(_normalized_value_equal("a@x.com", "a@x.com"))

    def test_case_insensitive(self) -> None:
        from openminion.services.agent.memory.learning import (
            _normalized_value_equal,
        )

        self.assertTrue(_normalized_value_equal("A@X.COM", "a@x.com"))

    def test_whitespace_collapse(self) -> None:
        from openminion.services.agent.memory.learning import (
            _normalized_value_equal,
        )

        self.assertTrue(_normalized_value_equal("a @ x . com", "a @ x . com"))
        self.assertTrue(
            _normalized_value_equal(
                "user email is   a@x.com  ", "user email is a@x.com"
            )
        )

    def test_different_values_not_equal(self) -> None:
        from openminion.services.agent.memory.learning import (
            _normalized_value_equal,
        )

        self.assertFalse(_normalized_value_equal("a@x.com", "b@y.com"))
        self.assertFalse(_normalized_value_equal("TypeScript", "JavaScript"))
        self.assertFalse(_normalized_value_equal("us-east-1", "eu-west-2"))

    def test_empty_strings_equal(self) -> None:
        from openminion.services.agent.memory.learning import (
            _normalized_value_equal,
        )

        self.assertTrue(_normalized_value_equal("", ""))
        self.assertTrue(_normalized_value_equal("   ", ""))


class RetrievalFiltersSupersededTests(unittest.TestCase):
    def test_list_excludes_superseded_records(self) -> None:
        service = MemoryService(store=InMemoryMemoryStore())
        old = MemoryRecord(
            id="old",
            scope="agent:a1",
            type="fact",
            key="fact:user_email",
            content="a@x.com",
            created_at="t",
            updated_at="t",
        )
        new = MemoryRecord(
            id="new",
            scope="agent:a1",
            type="fact",
            key="fact:user_email",
            content="b@y.com",
            created_at="t",
            updated_at="t",
        )
        service._store._records._records[old.id] = old
        service._store._records._records[new.id] = new
        service.supersede_by_contradiction(old.id, new.id, reason="test")

        hits = service.list(
            ListQueryOptions(scopes=["agent:a1"], types=["fact"], limit=None)
        )
        ids = {r.id for r in hits}
        self.assertIn("new", ids)
        self.assertNotIn("old", ids)


class EndToEndSupersessionTests(unittest.TestCase):
    def test_value_replacement_supersedes_old_record(self) -> None:
        service = MemoryService(store=InMemoryMemoryStore())
        # Stage + approve + promote first value.
        cid_old = service.stage_candidate(
            scope="agent:a1",
            record_type="fact",
            title="user email",
            content="a@x.com",
            meta={"normalized_key": "fact:user_email", "source": "auto_extracted"},
            confidence=0.7,
        )
        service.candidate_update(cid_old, {"status": "approved"})
        old_record = service.promote_candidate(cid_old, "agent:a1")
        self.assertEqual(old_record.key, "fact:user_email")

        # Stage + approve + promote replacement value.
        cid_new = service.stage_candidate(
            scope="agent:a1",
            record_type="fact",
            title="user email",
            content="b@y.com",
            meta={"normalized_key": "fact:user_email", "source": "auto_extracted"},
            confidence=0.8,
        )
        service.candidate_update(cid_new, {"status": "approved"})
        new_record = service.promote_candidate(cid_new, "agent:a1")

        # The store's promote_candidate detects the key collision
        # and marks the old record superseded.
        refreshed_old = service._store._records._records[old_record.id]
        self.assertTrue(refreshed_old.superseded)
        self.assertEqual(refreshed_old.superseded_by_id, new_record.id)

        # find_record_by_normalized_key returns ONLY the live record.
        live = service.find_record_by_normalized_key(
            scope="agent:a1",
            record_type="fact",
            normalized_key="fact:user_email",
        )
        self.assertIsNotNone(live)
        self.assertEqual(live.id, new_record.id)
        self.assertEqual(live.content, "b@y.com")

    def test_keyless_write_does_not_activate_key_path(self) -> None:
        service = MemoryService(store=InMemoryMemoryStore())
        cid = service.stage_candidate(
            scope="agent:a1",
            record_type="fact",
            title="free-form note",
            content="some free-form fact with no key",
            confidence=0.8,
            # No normalized_key in meta.
        )
        candidate = service.candidate_get(cid)
        self.assertIsNone(candidate.key)
