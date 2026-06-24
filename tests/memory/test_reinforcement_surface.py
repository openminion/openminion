from __future__ import annotations

import unittest
from typing import Any

from openminion.modules.memory.runtime.staging import (
    ExtractedCandidateDTO,
    stage_extracted_candidates,
)
from openminion.modules.memory.config import CandidateLearningConfig
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import CandidateListOptions
from openminion.modules.memory.storage.memory import InMemoryMemoryStore


def _dto(*, normalized_key: str, title: str, content: str) -> ExtractedCandidateDTO:
    return ExtractedCandidateDTO(
        kind="fact",
        normalized_key=normalized_key,
        title=title,
        content=content,
    )


def _proposed_candidates(service: MemoryService) -> list[Any]:
    return service.candidate_list(
        CandidateListOptions(proposed_scope="agent:agent-x", status="proposed")
    )


def _stage_turn(
    memory_service: Any,
    dto: ExtractedCandidateDTO,
    *,
    trace_id: str,
    session_id: str = "s1",
):
    return stage_extracted_candidates(
        memory_service=memory_service,
        session_id=session_id,
        agent_id="agent-x",
        trace_id=trace_id,
        candidates=[dto],
    )


class ProductionReinforcementSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryMemoryStore()
        self.service = MemoryService(store=self.store)

    def test_find_candidate_by_normalized_key_returns_none_when_empty(self) -> None:
        self.assertIsNone(
            self.service.find_candidate_by_normalized_key(
                scope="agent:agent-x", normalized_key="fact:user_name"
            )
        )

    def test_find_candidate_by_normalized_key_returns_none_for_blank_inputs(
        self,
    ) -> None:
        self.assertIsNone(
            self.service.find_candidate_by_normalized_key(
                scope="", normalized_key="fact:user_name"
            )
        )
        self.assertIsNone(
            self.service.find_candidate_by_normalized_key(
                scope="agent:x", normalized_key=""
            )
        )

    def test_find_matches_candidate_staged_with_normalized_key(self) -> None:
        cid = self.service.stage_candidate(
            scope="agent:agent-x",
            record_type="fact",
            title="user name",
            content="Jay",
            meta={"normalized_key": "fact:user_name"},
        )
        found = self.service.find_candidate_by_normalized_key(
            scope="agent:agent-x", normalized_key="fact:user_name"
        )
        self.assertEqual(found, cid)

    def test_find_does_not_match_different_scope(self) -> None:
        self.service.stage_candidate(
            scope="agent:agent-x",
            record_type="fact",
            title="t",
            content="c",
            meta={"normalized_key": "fact:user_name"},
        )
        self.assertIsNone(
            self.service.find_candidate_by_normalized_key(
                scope="agent:agent-y", normalized_key="fact:user_name"
            )
        )

    def test_reinforce_candidate_increments_counter_and_boosts_confidence(
        self,
    ) -> None:
        cid = self.service.stage_candidate(
            scope="agent:agent-x",
            record_type="fact",
            title="user name",
            content="Jay",
            confidence=0.3,
            meta={"normalized_key": "fact:user_name"},
        )
        updated = self.service.reinforce_candidate(candidate_id=cid)
        self.assertEqual(updated.meta.get("reconfirmation_count"), 1)
        self.assertAlmostEqual(updated.confidence, 0.4, places=6)

    def test_reinforce_candidate_clamps_confidence_at_max(self) -> None:
        cid = self.service.stage_candidate(
            scope="agent:agent-x",
            record_type="fact",
            title="t",
            content="c",
            confidence=0.85,
            meta={"normalized_key": "fact:x"},
        )
        updated = self.service.reinforce_candidate(candidate_id=cid)
        self.assertAlmostEqual(updated.confidence, 0.9, places=6)
        updated2 = self.service.reinforce_candidate(candidate_id=cid)
        self.assertAlmostEqual(updated2.confidence, 0.9, places=6)

    def test_operator_tuned_boost_via_set_candidate_learning_config(
        self,
    ) -> None:
        cfg = CandidateLearningConfig(
            confidence_boost_per_reconfirmation=0.25,
            confidence_max=0.8,
        )
        self.service.set_candidate_learning_config(cfg)
        cid = self.service.stage_candidate(
            scope="agent:agent-x",
            record_type="fact",
            title="t",
            content="c",
            confidence=0.3,
            meta={"normalized_key": "fact:x"},
        )
        updated = self.service.reinforce_candidate(candidate_id=cid)
        self.assertAlmostEqual(updated.confidence, 0.55, places=6)
        updated2 = self.service.reinforce_candidate(candidate_id=cid)
        self.assertAlmostEqual(updated2.confidence, 0.8, places=6)
        self.assertEqual(updated2.meta.get("reconfirmation_count"), 2)

    def test_stage_extracted_candidates_e2e_reinforces_production_service(
        self,
    ) -> None:
        dto = _dto(
            normalized_key="fact:user_name",
            title="user name",
            content="Jay",
        )

        # Turn 1: first mention → new candidate.
        r1 = _stage_turn(self.service, dto, trace_id="trace-1")
        r2 = _stage_turn(self.service, dto, trace_id="trace-2")
        r3 = _stage_turn(self.service, dto, trace_id="trace-3")
        proposed = _proposed_candidates(self.service)
        self.assertEqual(len(proposed), 1)

        candidate = proposed[0]
        self.assertEqual(candidate.meta.get("reconfirmation_count"), 2)
        self.assertEqual(candidate.meta.get("source"), "auto_extracted")
        self.assertEqual(candidate.meta.get("normalized_key"), "fact:user_name")

        self.assertEqual(r1.candidate_ids, r2.candidate_ids)
        self.assertEqual(r2.candidate_ids, r3.candidate_ids)

    def test_same_key_different_value_stages_fresh_candidate(self) -> None:
        old_dto = _dto(
            normalized_key="fact:user_email",
            title="user email",
            content="a@example.com",
        )
        new_dto = _dto(
            normalized_key="fact:user_email",
            title="user email",
            content="b@example.com",
        )
        r1 = _stage_turn(self.service, old_dto, trace_id="trace-1")
        r2 = _stage_turn(
            self.service,
            new_dto,
            trace_id="trace-2",
            session_id="s2",
        )
        proposed = _proposed_candidates(self.service)
        self.assertEqual(len(proposed), 2)
        self.assertNotEqual(r1.candidate_ids, r2.candidate_ids)
        self.assertEqual(
            {str(item.content) for item in proposed},
            {
                "a@example.com",
                "b@example.com",
            },
        )

    def test_stage_extracted_candidates_does_not_reinforce_after_promotion(
        self,
    ) -> None:
        dto = _dto(
            normalized_key="fact:user_name",
            title="user name",
            content="Jay",
        )
        r1 = _stage_turn(self.service, dto, trace_id="trace-1")
        original_id = r1.candidate_ids[0]

        self.service.candidate_update(original_id, {"status": "rejected"})

        r2 = _stage_turn(self.service, dto, trace_id="trace-2")
        self.assertNotEqual(r2.candidate_ids[0], original_id)


class AdapterPathReinforcementTests(unittest.TestCase):
    def _adapter(self) -> tuple[Any, Any]:
        from openminion.modules.brain.adapters.memory.runtime import MemctlAdapter

        store = InMemoryMemoryStore()
        service = MemoryService(store=store)
        adapter = MemctlAdapter(service)
        return adapter, service

    def test_adapter_forwards_find_candidate_by_normalized_key(self) -> None:
        adapter, service = self._adapter()
        cid = service.stage_candidate(
            scope="agent:agent-x",
            record_type="fact",
            title="user name",
            content="Jay",
            meta={"normalized_key": "fact:user_name"},
        )
        # Lookup via the adapter, not the service directly.
        found = adapter.find_candidate_by_normalized_key(
            scope="agent:agent-x", normalized_key="fact:user_name"
        )
        self.assertEqual(found, cid)

    def test_adapter_forwards_reinforce_candidate(self) -> None:
        adapter, service = self._adapter()
        cid = service.stage_candidate(
            scope="agent:agent-x",
            record_type="fact",
            title="user name",
            content="Jay",
            confidence=0.3,
            meta={"normalized_key": "fact:user_name"},
        )
        # Reinforce via the adapter.
        updated = adapter.reinforce_candidate(candidate_id=cid)
        self.assertEqual(updated.meta.get("reconfirmation_count"), 1)
        self.assertAlmostEqual(updated.confidence, 0.4, places=6)

    def test_adapter_forwards_candidate_get_for_content_check(self) -> None:
        adapter, service = self._adapter()
        cid = service.stage_candidate(
            scope="agent:agent-x",
            record_type="fact",
            title="user email",
            content="a@example.com",
            meta={"normalized_key": "fact:user_email"},
        )

        fetched = adapter.candidate_get(cid)

        self.assertEqual(fetched.candidate_id, cid)
        self.assertEqual(fetched.content, "a@example.com")

    def test_adapter_find_returns_none_when_backend_lacks_surface(self) -> None:
        from openminion.modules.brain.adapters.memory.runtime import MemctlAdapter

        class _BareBackend:
            def stage_candidate(self, **kwargs: Any) -> str:
                return "cand_bare"

        adapter = MemctlAdapter(_BareBackend())
        # Find returns None (not an exception) when the backend lacks the
        # method — this is what lets the staging helper fall back to
        # plain staging without special-casing adapter type.
        self.assertIsNone(
            adapter.find_candidate_by_normalized_key(
                scope="agent:x", normalized_key="fact:y"
            )
        )

    def test_stage_extracted_candidates_reinforces_through_adapter(self) -> None:
        adapter, service = self._adapter()

        dto = _dto(
            normalized_key="fact:user_name",
            title="user name",
            content="Jay",
        )

        for trace in ("t1", "t2", "t3"):
            _stage_turn(adapter, dto, trace_id=trace)
        proposed = _proposed_candidates(service)
        # Exactly one proposed candidate across 3 same-key turns — the
        # blocker the reviewer repro'd (three duplicates) is cleared.
        self.assertEqual(
            len(proposed),
            1,
            f"adapter path must dedupe same-key DTOs; got {len(proposed)} candidates",
        )
        self.assertEqual(proposed[0].meta.get("reconfirmation_count"), 2)

    def test_stage_extracted_candidates_stages_replacement_through_adapter(
        self,
    ) -> None:
        adapter, service = self._adapter()

        old_dto = _dto(
            normalized_key="fact:user_email",
            title="user email",
            content="a@example.com",
        )
        new_dto = _dto(
            normalized_key="fact:user_email",
            title="user email",
            content="b@example.com",
        )
        _stage_turn(adapter, old_dto, trace_id="t1")
        _stage_turn(adapter, new_dto, trace_id="t2", session_id="s2")
        proposed = _proposed_candidates(service)
        self.assertEqual(len(proposed), 2)
        self.assertEqual(
            {str(item.content) for item in proposed},
            {
                "a@example.com",
                "b@example.com",
            },
        )

    def test_factory_wires_candidate_learning_config_through_adapter(
        self,
    ) -> None:
        from openminion.modules.brain.adapters.factory.memory import (
            create_memory_adapter,
        )
        from openminion.modules.memory.config import (
            CandidateLearningConfig,
            SQLiteRuntimeConfig,
            StoreConfig,
        )
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            sqlite_path = Path(tmp) / "mem.db"
            tuned = CandidateLearningConfig(
                confidence_boost_per_reconfirmation=0.25,
                confidence_max=0.8,
            )
            fake_config = SimpleNamespace(
                store=StoreConfig(
                    backend="sqlite",
                    sqlite_path=sqlite_path,
                    sqlite=SQLiteRuntimeConfig(
                        wal_mode=True, busy_timeout_ms=5000, fts5_enabled=True
                    ),
                ),
                candidate_learning=tuned,
            )
            adapter = create_memory_adapter(
                mode="strict",
                db_path=str(sqlite_path),
                config=fake_config,
            )
            # The underlying service must now carry the tuned config.
            service = adapter._backend  # MemctlAdapter._backend is MemoryService
            self.assertIs(
                getattr(service, "_candidate_learning_config", None),
                tuned,
                "factory did not wire CandidateLearningConfig through adapter",
            )
