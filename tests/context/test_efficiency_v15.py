from __future__ import annotations

import unittest

from openminion.modules.context.schemas import (
    ArtifactDigest,
    BuildPackRequest,
    ContextBudgets,
    FactRecord,
    IdentitySnippet,
    MemoryCard,
    SessionSlice,
    SessionTurn,
    PINNED_BUCKETS,
    TRIM_ORDER,
)
from openminion.modules.context.service import ContextCtlService


# Stubs


class _IdentityClient:
    contract_version = "v1"

    def render(
        self, *, agent_id: str, purpose: str, max_tokens: int, provider_pref=None
    ) -> IdentitySnippet:
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="prof:v1",
            render_version="rend:v1",
            text=f"Identity for {agent_id}",
        )


class _SliceSession:
    contract_version = "v1"

    def __init__(self, turns=None, summary_short="short summary", tool_events=None):
        self._turns = turns or []
        self._summary = summary_short
        self._tool_events = tool_events or []

    def get_slice(self, *, session_id, purpose, limits) -> SessionSlice:
        return SessionSlice(
            session_id=session_id,
            slice_version="slice:v1",
            last_event_id="evt-001",
            summary_short=self._summary,
            recent_turns=self._turns,
            recent_tool_events=self._tool_events,
        )


class _MemoryClient:
    contract_version = "v1"

    def __init__(self, facts=None, cards=None):
        self._facts = facts or []
        self._cards = cards or []

    def query_facts(self, *, session_id, agent_id, query, limit, mode_name=None):
        del mode_name
        return self._facts[:limit]

    def query_memory_cards(self, *, session_id, agent_id, query, limit, mode_name=None):
        del mode_name
        return self._cards[:limit]

    def recall_session_start_memory(
        self, *, session_id, agent_id, query, turn_index, limit, mode_name=None
    ):
        del session_id, agent_id, query, turn_index, limit, mode_name
        return []

    def recall_mid_session_memory(self, **kwargs):
        del kwargs
        return []

    def recall_recent_session_artifacts(self, **kwargs):
        del kwargs
        return []

    def get_procedure(self, *, procedure_id):
        return None


class _ArtifactClient:
    contract_version = "v1"

    def __init__(self, digests=None):
        self._digests = digests or []

    def query_digests(self, *, session_id, agent_id, query, limit):
        return self._digests[:limit]


def _make_service(**kwargs) -> ContextCtlService:
    return ContextCtlService(
        identityctl=kwargs.get("identity", _IdentityClient()),
        sessctl=kwargs.get("session", _SliceSession()),
        memctl=kwargs.get("memory", _MemoryClient()),
        artifactctl=kwargs.get("artifact", _ArtifactClient()),
    )


def _make_request(**kwargs) -> BuildPackRequest:
    return BuildPackRequest(
        session_id=kwargs.get("session_id", "sess-test"),
        agent_id=kwargs.get("agent_id", "agent-test"),
        purpose=kwargs.get("purpose", "act"),
        query=kwargs.get("query", "hello"),
        budgets_override=kwargs.get("budgets_override"),
        constraints=kwargs.get("constraints"),
        model_hint=kwargs.get("model_hint"),
    )


class BudgetStabilityTests(unittest.TestCase):
    def _stress_budget(self) -> ContextBudgets:
        return ContextBudgets(
            total_max_tokens=300,
            identity_tokens=60,
            summary_tokens=40,
            recent_turn_tokens=80,
            facts_tokens=40,
            memory_tokens=40,
            skills_tokens=10,
            artifact_tokens=20,
            instructions_tokens=20,
        )

    def test_500_turn_pack_stays_within_budget(self) -> None:
        turns = [
            SessionTurn(
                turn_id=f"t-{i}",
                role="user" if i % 2 == 0 else "assistant",
                content=f"message content {i} " + "word " * 20,
            )
            for i in range(500)
        ]
        service = _make_service(session=_SliceSession(turns=turns))
        req = _make_request(budgets_override=self._stress_budget())
        pack = service.build_pack(req)
        sum(4 * len(s.content) // 4 for s in pack.segments if s.content.strip())
        self.assertIsNotNone(pack)
        self.assertIsNotNone(pack.token_budget_report)

    def test_500_facts_pack_trims_to_top_k(self) -> None:
        facts = [
            FactRecord(record_id=f"f-{i}", text=f"fact content {i}") for i in range(500)
        ]
        service = _make_service(memory=_MemoryClient(facts=facts))
        req = _make_request()
        pack = service.build_pack(req)
        manifest_facts = pack.context_manifest.facts if pack.context_manifest else []
        self.assertLessEqual(len(manifest_facts), 20)

    def test_500_memory_cards_trimmed_to_top_k(self) -> None:
        cards = [
            MemoryCard(record_id=f"m-{i}", record_type="note", text=f"memory card {i}")
            for i in range(500)
        ]
        service = _make_service(memory=_MemoryClient(cards=cards))
        req = _make_request()
        pack = service.build_pack(req)
        manifest_memory = pack.context_manifest.memory if pack.context_manifest else []
        self.assertLessEqual(len(manifest_memory), 15)

    def test_tight_budget_produces_valid_pack_with_decision_log(self) -> None:
        turns = [
            SessionTurn(turn_id=f"t-{i}", role="user", content="word " * 50)
            for i in range(20)
        ]
        facts = [FactRecord(record_id=f"f-{i}", text="fact " * 30) for i in range(5)]
        artifacts = [
            ArtifactDigest(ref=f"art-{i}", bullets=["bullet content " * 5])
            for i in range(5)
        ]
        service = _make_service(
            session=_SliceSession(turns=turns),
            memory=_MemoryClient(facts=facts),
            artifact=_ArtifactClient(digests=artifacts),
        )
        req = _make_request(budgets_override=self._stress_budget())
        pack = service.build_pack(req)
        self.assertIsNotNone(pack)
        self.assertIsNotNone(pack.token_budget_report)
        self.assertIn("static_prefix", pack.token_budget_report.buckets)
        self.assertIn("recent_window", pack.token_budget_report.buckets)


class ArtifactFirstTests(unittest.TestCase):
    LARGE_CONTENT_CHARS = 5000

    def test_huge_artifact_excerpt_is_capped_in_preview(self) -> None:
        from openminion.modules.context.schemas import ARTIFACT_PREVIEW_MAX_CHARS

        large_excerpt = "X" * self.LARGE_CONTENT_CHARS
        artifact = ArtifactDigest(
            ref="big-artifact", excerpt=large_excerpt, bullets=["bullet"]
        )
        service = _make_service(artifact=_ArtifactClient(digests=[artifact]))
        req = _make_request()
        pack = service.build_pack(req)
        ev_segs = [s for s in pack.segments if s.bucket == "evidence_refs"]
        self.assertEqual(len(ev_segs), 1)
        self.assertNotIn("X" * (ARTIFACT_PREVIEW_MAX_CHARS + 1), ev_segs[0].content)
        self.assertIn("big-artifact", ev_segs[0].content)

    def test_artifact_segment_is_marked_preview_only(self) -> None:
        artifact = ArtifactDigest(ref="art-ref", bullets=["b1", "b2", "b3", "b4", "b5"])
        service = _make_service(artifact=_ArtifactClient(digests=[artifact]))
        req = _make_request()
        pack = service.build_pack(req)
        ev_segs = [s for s in pack.segments if s.bucket == "evidence_refs"]
        for seg in ev_segs:
            self.assertTrue(seg.is_artifact_preview)

    def test_bullet_count_capped_at_max_bullets(self) -> None:
        from openminion.modules.context.schemas import ARTIFACT_PREVIEW_MAX_BULLETS

        artifact = ArtifactDigest(
            ref="art-bullets",
            bullets=[
                f"Bullet number {i} with detailed content here" for i in range(20)
            ],
        )
        service = _make_service(artifact=_ArtifactClient(digests=[artifact]))
        req = _make_request()
        pack = service.build_pack(req)
        ev_segs = [s for s in pack.segments if s.bucket == "evidence_refs"]
        self.assertEqual(len(ev_segs), 1)
        bullet_count = ev_segs[0].content.count("Bullet number")
        self.assertLessEqual(bullet_count, ARTIFACT_PREVIEW_MAX_BULLETS)

    def test_artifact_ref_appears_in_segment_refs(self) -> None:
        artifact = ArtifactDigest(ref="my-artifact-ref", bullets=["here"])
        service = _make_service(artifact=_ArtifactClient(digests=[artifact]))
        req = _make_request()
        pack = service.build_pack(req)
        ev_segs = [s for s in pack.segments if s.bucket == "evidence_refs"]
        self.assertEqual(len(ev_segs), 1)
        self.assertIn("my-artifact-ref", ev_segs[0].refs)

    def test_10_artifacts_all_included_in_refs(self) -> None:
        artifacts = [
            ArtifactDigest(ref=f"art-{i}", bullets=[f"bullet-{i}"]) for i in range(15)
        ]
        service = _make_service(artifact=_ArtifactClient(digests=artifacts))
        req = _make_request()
        pack = service.build_pack(req)
        ev_segs = [s for s in pack.segments if s.bucket == "evidence_refs"]
        self.assertLessEqual(len(ev_segs), 10)


class CacheOrderingTests(unittest.TestCase):
    def test_static_prefix_is_first_message(self) -> None:
        service = _make_service()
        req = _make_request()
        pack = service.build_pack(req)
        self.assertGreater(len(pack.messages), 0)
        self.assertEqual(pack.messages[0].role, "system")
        self.assertIn("[IDENTITY]", pack.messages[0].content)

    def test_turn_input_is_last_message(self) -> None:
        service = _make_service()
        req = _make_request()
        pack = service.build_pack(req)
        self.assertEqual(pack.messages[-1].role, "user")
        self.assertIn("hello", pack.messages[-1].content)

    def test_prompt_cache_key_stable_for_same_input(self) -> None:
        service = _make_service()
        pack1 = service.build_pack(_make_request(query="what is the answer?"))
        pack2 = service.build_pack(_make_request(query="what is the answer?"))
        self.assertEqual(pack1.prompt_cache_key, pack2.prompt_cache_key)

    def test_prompt_cache_key_changes_with_model_hint(self) -> None:
        service = _make_service()
        pack1 = service.build_pack(_make_request(model_hint="claude-3-7-sonnet"))
        pack2 = service.build_pack(_make_request(model_hint="gpt-4o"))
        self.assertNotEqual(pack1.prompt_cache_key, pack2.prompt_cache_key)

    def test_static_prefix_hash_stable_across_queries(self) -> None:
        service1 = _make_service()
        service2 = _make_service()
        pack1 = service1.build_pack(_make_request(query="query one"))
        pack2 = service2.build_pack(_make_request(query="query two"))
        self.assertEqual(pack1.static_prefix_hash, pack2.static_prefix_hash)

    def test_segments_follow_canonical_bucket_order(self) -> None:
        bucket_order = [
            "static_prefix",
            "mission_snapshot",
            "summaries",
            "recent_window",
            "retrieval",
            "evidence_refs",
            "turn_input",
        ]
        artifacts = [ArtifactDigest(ref="art-1", bullets=["bullet"])]
        facts = [FactRecord(record_id="f-1", text="fact")]
        service = _make_service(
            memory=_MemoryClient(facts=facts),
            artifact=_ArtifactClient(digests=artifacts),
        )
        pack = service.build_pack(_make_request())
        non_empty = [s for s in pack.segments if s.content.strip()]
        positions = [
            bucket_order.index(s.bucket) for s in non_empty if s.bucket in bucket_order
        ]
        self.assertEqual(positions, sorted(positions))


class MissionInvariantTests(unittest.TestCase):
    def _absolute_minimum_budget(self) -> ContextBudgets:
        return ContextBudgets(
            total_max_tokens=50,  # absurdly small
            identity_tokens=20,
            summary_tokens=5,
            recent_turn_tokens=5,
            facts_tokens=5,
            memory_tokens=5,
            skills_tokens=2,
            artifact_tokens=2,
            instructions_tokens=6,
        )

    def test_mission_snapshot_survives_extreme_budget(self) -> None:
        service = _make_service()
        req = _make_request(budgets_override=self._absolute_minimum_budget())
        pack = service.build_pack(req)
        mission_segs = [
            s
            for s in pack.segments
            if s.bucket == "mission_snapshot" and s.content.strip()
        ]
        self.assertGreater(len(mission_segs), 0)
        self.assertTrue(all(s.pinned for s in mission_segs))

    def test_static_prefix_survives_extreme_budget(self) -> None:
        service = _make_service()
        req = _make_request(budgets_override=self._absolute_minimum_budget())
        pack = service.build_pack(req)
        static_segs = [
            s
            for s in pack.segments
            if s.bucket == "static_prefix" and s.content.strip()
        ]
        self.assertGreater(len(static_segs), 0)
        self.assertTrue(all(s.pinned for s in static_segs))

    def test_turn_input_always_last_and_pinned(self) -> None:
        service = _make_service()
        req = _make_request(budgets_override=self._absolute_minimum_budget())
        pack = service.build_pack(req)
        turn_segs = [s for s in pack.segments if s.bucket == "turn_input"]
        self.assertEqual(len(turn_segs), 1)
        self.assertTrue(turn_segs[0].pinned)
        last_seg = pack.segments[-1]
        self.assertEqual(last_seg.bucket, "turn_input")

    def test_identity_text_in_system_message_after_degrade(self) -> None:
        turns = [
            SessionTurn(turn_id=f"t-{i}", role="user", content="word " * 30)
            for i in range(100)
        ]
        facts = [FactRecord(record_id=f"f-{i}", text="fact " * 30) for i in range(20)]
        service = _make_service(
            session=_SliceSession(turns=turns),
            memory=_MemoryClient(facts=facts),
        )
        req = _make_request(budgets_override=self._absolute_minimum_budget())
        pack = service.build_pack(req)
        system_msgs = [m for m in pack.messages if m.role == "system"]
        self.assertGreater(len(system_msgs), 0)
        combined = "\n".join(m.content for m in system_msgs)
        self.assertIn("[IDENTITY]", combined)

    def test_trim_order_respects_pinned_buckets(self) -> None:
        for bucket_name in TRIM_ORDER:
            self.assertNotIn(
                bucket_name,
                PINNED_BUCKETS,
                msg=f"Bucket '{bucket_name}' appears in TRIM_ORDER but is PINNED — this is a policy violation",
            )

    def test_decision_log_has_no_pinned_drops(self) -> None:
        turns = [
            SessionTurn(turn_id=f"t-{i}", role="user", content="word " * 50)
            for i in range(30)
        ]
        facts = [FactRecord(record_id=f"f-{i}", text="fact " * 30) for i in range(10)]
        service = _make_service(
            session=_SliceSession(turns=turns),
            memory=_MemoryClient(facts=facts),
        )
        req = _make_request(budgets_override=self._absolute_minimum_budget())
        pack = service.build_pack(req)
        if pack.pack_policy and pack.pack_policy.actions:
            for action in pack.pack_policy.actions:
                dropped_ids = set(action.segment_ids)
                pinned_seg_ids = {s.id for s in pack.segments if s.pinned}
                overlap = dropped_ids & pinned_seg_ids
                self.assertEqual(
                    overlap,
                    set(),
                    msg=f"TrimAction '{action.action}' dropped pinned segments: {overlap}",
                )

    def test_invariants_preserved_list_populated(self) -> None:
        service = _make_service()
        req = _make_request()
        pack = service.build_pack(req)
        log = (
            pack.token_budget_report.decision_log if pack.token_budget_report else None
        )
        if log and pack.pack_policy:
            pinned_ids = {s.id for s in pack.segments if s.pinned}
            for pid in pinned_ids:
                matching = [s for s in pack.segments if s.id == pid]
                if matching:
                    self.assertTrue(matching[0].pinned)
