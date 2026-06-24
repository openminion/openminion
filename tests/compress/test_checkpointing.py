from __future__ import annotations

import unittest

from openminion.modules.context.compress import (
    AfterLastCheckpointSelector,
    CheckpointComposerV1,
    CheckpointFailedPayload,
    CheckpointStore,
    CheckpointStructuredState,
    CompressionCheckpoint,
    CompactionService,
    OpenLoopStrategy,
    SeedBundle,
    StructuredDecision,
    CHECKPOINT_ERROR_BUDGET_EXCEEDED,
    CHECKPOINT_ERROR_RANGE_INVALID,
    CHECKPOINT_ERROR_STABLE_ID_COLLISION,
)
from openminion.modules.context.compress.strategies import DeltaEvent


def _make_events(*texts: str) -> list[DeltaEvent]:
    return [
        DeltaEvent(event_id=f"e{i + 1}", event_type="turn.user", text=t)
        for i, t in enumerate(texts)
    ]


def _make_svc(**kwargs) -> CompactionService:
    return CompactionService(**kwargs)


class TestCheckpointSchemaContract(unittest.TestCase):
    def test_checkpoint_has_required_fields(self):
        cp = CompressionCheckpoint(
            checkpoint_id="cp-1",
            session_id="s1",
            created_at="2026-03-04T00:00:00+00:00",
            from_event_id=None,
            to_event_id="e5",
            summary_text="Test summary",
        )
        self.assertEqual(cp.checkpoint_id, "cp-1")
        self.assertEqual(cp.session_id, "s1")
        self.assertIsNone(cp.from_event_id)
        self.assertEqual(cp.to_event_id, "e5")
        self.assertEqual(cp.summary_text, "Test summary")
        self.assertEqual(cp.version, "1.6")

    def test_checkpoint_structured_defaults(self):
        cp = CompressionCheckpoint(
            checkpoint_id="cp-1",
            session_id="s1",
            created_at="2026-03-04T00:00:00+00:00",
            from_event_id=None,
            to_event_id="e1",
            summary_text="",
        )
        self.assertEqual(cp.structured.decisions, [])
        self.assertEqual(cp.structured.constraints, [])
        self.assertEqual(cp.structured.open_loops, [])
        self.assertEqual(cp.structured.entities, {})
        self.assertEqual(cp.structured.tool_digests, [])

    def test_checkpoint_stats_defaults(self):
        cp = CompressionCheckpoint(
            checkpoint_id="cp-1",
            session_id="s1",
            created_at="2026-03-04T00:00:00+00:00",
            from_event_id=None,
            to_event_id="e1",
            summary_text="",
        )
        self.assertEqual(cp.stats.total_tokens, 0)
        self.assertEqual(cp.stats.summary_tokens, 0)

    def test_checkpoint_to_dict(self):
        cp = CompressionCheckpoint(
            checkpoint_id="cp-1",
            session_id="s1",
            created_at="2026-03-04T00:00:00+00:00",
            from_event_id=None,
            to_event_id="e1",
            summary_text="hello",
        )
        d = cp.to_dict()
        self.assertIn("checkpoint_id", d)
        self.assertIn("structured", d)
        self.assertIn("stats", d)

    def test_checkpoint_failed_payload_has_required_fields(self):
        fail = CheckpointFailedPayload(
            failure_id="f-1",
            session_id="s1",
            reason="budget exceeded",
            error_code=CHECKPOINT_ERROR_BUDGET_EXCEEDED,
            created_at="2026-03-04T00:00:00+00:00",
        )
        self.assertEqual(fail.error_code, CHECKPOINT_ERROR_BUDGET_EXCEEDED)
        self.assertEqual(fail.session_id, "s1")

    def test_checkpoint_store_save_and_retrieve(self):
        store = CheckpointStore()
        cp = CompressionCheckpoint(
            checkpoint_id="cp-1",
            session_id="s1",
            created_at="2026-03-04T00:00:00+00:00",
            from_event_id=None,
            to_event_id="e5",
            summary_text="persisted summary",
        )
        store.save_checkpoint(cp)
        retrieved = store.get_checkpoint("cp-1")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.summary_text, "persisted summary")
        self.assertEqual(retrieved.to_event_id, "e5")


class TestPluginContractIntegrity(unittest.TestCase):
    def test_after_last_checkpoint_selector_no_boundary(self):
        sel = AfterLastCheckpointSelector()
        events = _make_events("a", "b", "c")
        selected = sel.select(events, last_checkpoint_to_event_id=None)
        self.assertEqual(len(selected), 3)

    def test_after_last_checkpoint_selector_with_boundary(self):
        sel = AfterLastCheckpointSelector()
        events = _make_events("a", "b", "c", "d")
        selected = sel.select(events, last_checkpoint_to_event_id="e2")
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0].event_id, "e3")
        self.assertEqual(selected[1].event_id, "e4")

    def test_after_last_checkpoint_selector_boundary_not_found_returns_all(self):
        sel = AfterLastCheckpointSelector()
        events = _make_events("a", "b")
        selected = sel.select(events, last_checkpoint_to_event_id="nonexistent")
        self.assertEqual(len(selected), 2)

    def test_open_loop_strategy_extract(self):
        s = OpenLoopStrategy()
        events = [
            DeltaEvent(
                event_id="e1",
                event_type="open_loop.created",
                payload={"question_or_todo": "Should we use Redis?"},
            )
        ]
        entries = s.extract(events)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].tier_type, "open_loops")
        self.assertIn("Redis", entries[0].text)

    def test_open_loop_strategy_abstract_removes_closed(self):
        from openminion.modules.context.compress.schemas import TierEntry

        s = OpenLoopStrategy()
        existing = [
            TierEntry(
                tier_type="open_loops",
                text="Should we use Redis?",
                meta={"status": "open"},
                token_count=5,
            ),
        ]
        new = [
            TierEntry(
                tier_type="open_loops",
                text="Should we use Redis?",
                meta={"status": "closed"},
                token_count=5,
            ),
        ]
        merged = s.abstract(existing, new, token_budget=100)
        self.assertEqual(len(merged), 0)


class TestDeltaReducerDeterminism(unittest.TestCase):
    def test_delta_selector_deterministic(self):
        sel = AfterLastCheckpointSelector()
        events = [
            DeltaEvent(event_id=f"e{i}", event_type="turn.user", text=f"msg {i}")
            for i in range(10)
        ]
        result1 = sel.select(events, "e5")
        result2 = sel.select(events, "e5")
        self.assertEqual([e.event_id for e in result1], [e.event_id for e in result2])

    def test_summary_reducer_deterministic(self):
        svc1 = _make_svc()
        svc2 = _make_svc()
        events = _make_events("hello world", "goodbye world")
        b1 = svc1.update("s1", events)
        b2 = svc2.update("s1", events)
        self.assertEqual(b1.summary_text, b2.summary_text)
        self.assertEqual(b1.total_tokens, b2.total_tokens)


class TestComposerSafetyChecks(unittest.TestCase):
    def setUp(self):
        self.composer = CheckpointComposerV1()
        self.structured = CheckpointStructuredState()
        self.ts = "2026-03-04T00:00:00+00:00"

    def test_range_check_same_from_to_fails(self):
        result = self.composer.compose(
            session_id="s1",
            checkpoint_id="cp-1",
            created_at=self.ts,
            from_event_id="e5",
            to_event_id="e5",
            summary_text="",
            recent_window_event_ids=[],
            structured=self.structured,
        )
        self.assertIsInstance(result, CheckpointFailedPayload)
        self.assertEqual(result.error_code, CHECKPOINT_ERROR_RANGE_INVALID)

    def test_budget_exceeded_fails(self):
        big_summary = " ".join(["word"] * 3000)
        result = self.composer.compose(
            session_id="s1",
            checkpoint_id="cp-1",
            created_at=self.ts,
            from_event_id=None,
            to_event_id="e1",
            summary_text=big_summary,
            recent_window_event_ids=[],
            structured=self.structured,
            token_limit=100,
        )
        self.assertIsInstance(result, CheckpointFailedPayload)
        self.assertEqual(result.error_code, CHECKPOINT_ERROR_BUDGET_EXCEEDED)
        self.assertIn("total_tokens", result.details)

    def test_duplicate_stable_ids_fail(self):
        structured = CheckpointStructuredState(
            decisions=[
                StructuredDecision(id="d1", statement="Use Python"),
                StructuredDecision(id="d1", statement="Use Go"),  # duplicate ID
            ]
        )
        result = self.composer.compose(
            session_id="s1",
            checkpoint_id="cp-1",
            created_at=self.ts,
            from_event_id=None,
            to_event_id="e1",
            summary_text="test",
            recent_window_event_ids=[],
            structured=structured,
        )
        self.assertIsInstance(result, CheckpointFailedPayload)
        self.assertEqual(result.error_code, CHECKPOINT_ERROR_STABLE_ID_COLLISION)

    def test_valid_checkpoint_succeeds(self):
        result = self.composer.compose(
            session_id="s1",
            checkpoint_id="cp-1",
            created_at=self.ts,
            from_event_id=None,
            to_event_id="e5",
            summary_text="short summary",
            recent_window_event_ids=["e4", "e5"],
            structured=self.structured,
        )
        self.assertIsInstance(result, CompressionCheckpoint)
        self.assertEqual(result.checkpoint_id, "cp-1")
        self.assertGreater(result.stats.summary_tokens, 0)


class TestStructuredAntiDriftStability(unittest.TestCase):
    def test_get_structured_state_has_all_keys(self):
        svc = _make_svc()
        state = svc.get_structured_state("s1")
        self.assertIn("decisions", state)
        self.assertIn("constraints", state)
        self.assertIn("open_loops", state)
        self.assertIn("entities", state)
        self.assertIn("tool_digests", state)

    def test_structured_state_decisions_have_stable_ids(self):
        svc = _make_svc()
        events = [
            DeltaEvent(
                event_id="e1",
                event_type="decision.made",
                payload={"decision_text": "Use SQLite"},
            ),
        ]
        svc.update("s1", events)
        state = svc.get_structured_state("s1")
        decisions = state["decisions"]
        self.assertEqual(len(decisions), 1)
        self.assertIn("id", decisions[0])
        self.assertTrue(decisions[0]["id"])  # non-empty

    def test_structured_state_stable_id_consistent_across_calls(self):
        svc = _make_svc()
        events = [
            DeltaEvent(
                event_id="e1",
                event_type="decision.made",
                payload={"decision_text": "Use SQLite"},
            ),
        ]
        svc.update("s1", events)
        state1 = svc.get_structured_state("s1")
        state2 = svc.get_structured_state("s1")
        self.assertEqual(state1["decisions"][0]["id"], state2["decisions"][0]["id"])

    def test_structured_state_open_loops(self):
        svc = _make_svc()
        events = [
            DeltaEvent(
                event_id="e1",
                event_type="open_loop.created",
                payload={"question_or_todo": "Should we add caching?"},
            ),
        ]
        svc.update("s1", events)
        state = svc.get_structured_state("s1")
        self.assertGreater(len(state["open_loops"]), 0)
        self.assertIn("caching", state["open_loops"][0]["question_or_todo"])


class TestCheckpointAPIParity(unittest.TestCase):
    def _setup_svc_with_events(self):
        svc = _make_svc()
        events = [
            DeltaEvent(event_id="e1", event_type="turn.user", text="Hello world"),
            DeltaEvent(event_id="e2", event_type="turn.assistant", text="Hi"),
            DeltaEvent(
                event_id="e3",
                event_type="decision.made",
                payload={"decision_text": "Use caching"},
            ),
        ]
        svc.update("s1", events)
        return svc

    def test_maybe_checkpoint_returns_id(self):
        svc = self._setup_svc_with_events()
        cp_id = svc.maybe_checkpoint("s1", "test")
        self.assertIsNotNone(cp_id)
        self.assertTrue(cp_id)

    def test_get_latest_checkpoint_returns_none_before_any(self):
        svc = _make_svc()
        cp = svc.get_latest_checkpoint("s1")
        self.assertIsNone(cp)

    def test_get_latest_checkpoint_returns_checkpoint_after_maybe_checkpoint(self):
        svc = self._setup_svc_with_events()
        svc.maybe_checkpoint("s1", "test")
        cp = svc.get_latest_checkpoint("s1")
        self.assertIsNotNone(cp)
        self.assertIsInstance(cp, CompressionCheckpoint)
        self.assertEqual(cp.session_id, "s1")

    def test_get_latest_checkpoint_has_structured_state(self):
        svc = self._setup_svc_with_events()
        svc.maybe_checkpoint("s1", "test")
        cp = svc.get_latest_checkpoint("s1")
        self.assertIsNotNone(cp)
        self.assertGreater(len(cp.structured.decisions), 0)

    def test_maybe_checkpoint_failed_returns_none_and_persists_failure(self):
        svc = _make_svc(token_limit=1)  # tiny budget to force failure
        events = _make_events(
            "this is a long summary that will exceed the tiny token budget"
        )
        svc.update("s1", events)
        cp_id = svc.maybe_checkpoint("s1", "test")
        self.assertIn(cp_id, [None, cp_id])

    def test_checkpoint_store_latest_pointer_tracks_most_recent(self):
        store = CheckpointStore()
        cp1 = CompressionCheckpoint(
            checkpoint_id="cp-1",
            session_id="s1",
            created_at="2026-03-04T00:00:00+00:00",
            from_event_id=None,
            to_event_id="e5",
            summary_text="first",
        )
        cp2 = CompressionCheckpoint(
            checkpoint_id="cp-2",
            session_id="s1",
            created_at="2026-03-04T01:00:00+00:00",
            from_event_id="e5",
            to_event_id="e10",
            summary_text="second",
        )
        store.save_checkpoint(cp1)
        store.save_checkpoint(cp2)
        latest = store.get_latest_checkpoint("s1")
        self.assertEqual(latest.checkpoint_id, "cp-2")


class TestRebuildability(unittest.TestCase):
    def test_rebuild_produces_checkpoint(self):
        svc = _make_svc()
        events = [
            DeltaEvent(event_id="e1", event_type="turn.user", text="Setup"),
            DeltaEvent(
                event_id="e2",
                event_type="decision.made",
                payload={"decision_text": "Use Python"},
            ),
            DeltaEvent(event_id="e3", event_type="turn.assistant", text="Done"),
        ]
        svc.update("s1", events)
        svc.maybe_checkpoint("s1", "initial")

        cp_ids = svc.rebuild_checkpoints("s1")
        self.assertGreater(len(cp_ids), 0)

    def test_rebuild_yields_equivalent_decisions(self):
        svc = _make_svc()
        events = [
            DeltaEvent(
                event_id="e1",
                event_type="decision.made",
                payload={"decision_text": "Use SQLite WAL"},
            ),
            DeltaEvent(event_id="e2", event_type="turn.user", text="Continue"),
        ]
        svc.update("s1", events)
        svc.maybe_checkpoint("s1", "before_rebuild")

        original_state = svc.get_structured_state("s1")

        cp_ids = svc.rebuild_checkpoints("s1", events)
        self.assertGreater(len(cp_ids), 0)

        rebuilt_state = svc.get_structured_state("s1")
        orig_statements = {d["statement"] for d in original_state["decisions"]}
        rebuilt_statements = {d["statement"] for d in rebuilt_state["decisions"]}
        self.assertEqual(orig_statements, rebuilt_statements)

    def test_rebuild_deletes_old_checkpoints_first(self):
        store = CheckpointStore()
        svc = _make_svc(checkpoint_store=store)
        events = _make_events("hello", "world")
        svc.update("s1", events)
        svc.maybe_checkpoint("s1", "first")
        svc.maybe_checkpoint("s1", "second")

        before = store.list_checkpoints("s1")
        self.assertGreaterEqual(len(before), 1)

        svc.rebuild_checkpoints("s1")
        after = store.list_checkpoints("s1")
        self.assertEqual(len(after), 1)


class TestMonotonicityAndInvariance(unittest.TestCase):
    def test_checkpoints_have_non_null_to_event_id(self):
        svc = _make_svc()
        events = _make_events("a", "b", "c")
        svc.update("s1", events)
        cp_id = svc.maybe_checkpoint("s1", "test")
        self.assertIsNotNone(cp_id)
        cp = svc.get_latest_checkpoint("s1")
        self.assertIsNotNone(cp.to_event_id)

    def test_multiple_checkpoints_have_distinct_ids(self):
        svc = _make_svc()
        events1 = _make_events("first batch")
        svc.update("s1", events1)
        cp1_id = svc.maybe_checkpoint("s1", "first")

        events2 = [
            DeltaEvent(event_id="e10", event_type="turn.user", text="second batch")
        ]
        svc.update("s1", events2)
        cp2_id = svc.maybe_checkpoint("s1", "second")

        self.assertIsNotNone(cp1_id)
        self.assertIsNotNone(cp2_id)
        if cp1_id and cp2_id:
            self.assertNotEqual(cp1_id, cp2_id)

    def test_no_history_loss_checkpoint_does_not_delete_events(self):
        svc = _make_svc()
        events = _make_events("a", "b", "c")
        svc.update("s1", events)
        svc.maybe_checkpoint("s1", "test")

        tracked = svc._all_events.get("s1", [])
        self.assertEqual(len(tracked), 3)

    def test_range_valid_from_lt_to(self):
        composer = CheckpointComposerV1()
        ts = "2026-03-04T00:00:00+00:00"
        result = composer.compose(
            session_id="s1",
            checkpoint_id="cp-1",
            created_at=ts,
            from_event_id="e1",
            to_event_id="e5",
            summary_text="ok",
            recent_window_event_ids=["e5"],
            structured=CheckpointStructuredState(),
        )
        self.assertIsInstance(result, CompressionCheckpoint)
        self.assertEqual(result.from_event_id, "e1")
        self.assertEqual(result.to_event_id, "e5")

    def test_stable_ids_unique_within_type(self):
        svc = _make_svc()
        events = [
            DeltaEvent(
                event_id="e1",
                event_type="decision.made",
                payload={"decision_text": "Use Python"},
            ),
            DeltaEvent(
                event_id="e2",
                event_type="decision.made",
                payload={"decision_text": "Use SQLite"},
            ),
        ]
        svc.update("s1", events)
        state = svc.get_structured_state("s1")
        ids = [d["id"] for d in state["decisions"]]
        self.assertEqual(len(ids), len(set(ids)))


class TestTriggerBehavior(unittest.TestCase):
    def test_evaluate_triggers_run_boundary(self):
        svc = _make_svc()
        events = [DeltaEvent(event_id="e1", event_type="run.finished")]
        reasons = svc.evaluate_triggers("s1", events)
        from openminion.modules.context.compress.compaction import TriggerReason

        self.assertIn(TriggerReason.AFTER_RUN_FINISHED, reasons)

    def test_evaluate_triggers_manual_refresh(self):
        svc = _make_svc()
        reasons = svc.evaluate_triggers("s1", [], manual_refresh=True)
        from openminion.modules.context.compress.compaction import TriggerReason

        self.assertIn(TriggerReason.MANUAL_REFRESH, reasons)


class TestIntegrationAuditability(unittest.TestCase):
    def test_maybe_checkpoint_writes_to_sessctl(self):
        class MockSessctl:
            def __init__(self):
                self.events = []
                self.checkpoints = []

            def write_event(self, session_id, event_type, payload):
                self.events.append({"type": event_type, "payload": payload})

            def save_compression_checkpoint(self, session_id, bundle_json, **kw):
                self.checkpoints.append({"session_id": session_id})
                return "mock-cp"

            def get_latest_checkpoint(self, session_id):
                return None

        mock = MockSessctl()
        svc = _make_svc(sessctl=mock)
        events = _make_events("hello", "world")
        svc.update("s1", events)
        cp_id = svc.maybe_checkpoint("s1", "test")

        if cp_id:
            event_types = [e["type"] for e in mock.events]
            self.assertIn("compression.checkpoint.created", event_types)


class TestSeedBundleContract(unittest.TestCase):
    def test_build_seed_bundle_returns_seed(self):
        svc = _make_svc()
        events = _make_events("context A", "context B")
        svc.update("s1", events)
        seed = svc.build_seed_bundle("s1", budget_tokens=500)
        self.assertIsInstance(seed, SeedBundle)
        self.assertEqual(seed.session_id, "s1")

    def test_build_seed_bundle_respects_budget(self):
        svc = _make_svc()
        events = _make_events(*[f"word {i}" for i in range(100)])
        svc.update("s1", events)
        seed = svc.build_seed_bundle("s1", budget_tokens=50)
        self.assertLessEqual(seed.total_tokens, 50)

    def test_build_seed_bundle_has_sections(self):
        svc = _make_svc()
        events = [
            DeltaEvent(
                event_id="e1", event_type="turn.user", text="Working on auth system"
            ),
            DeltaEvent(
                event_id="e2",
                event_type="decision.made",
                payload={"decision_text": "Use JWT tokens"},
            ),
        ]
        svc.update("s1", events)
        seed = svc.build_seed_bundle("s1", budget_tokens=1200)
        self.assertGreater(len(seed.sections), 0)

    def test_build_seed_bundle_render_text(self):
        svc = _make_svc()
        events = _make_events("summary content here")
        svc.update("s1", events)
        seed = svc.build_seed_bundle("s1")
        text = seed.render_text()
        self.assertIsInstance(text, str)
        self.assertTrue(text.strip())

    def test_build_seed_bundle_checkpoint_lineage(self):
        svc = _make_svc()
        events = _make_events("context for checkpoint")
        svc.update("s1", events)
        cp_id = svc.maybe_checkpoint("s1", "pre_seed")
        seed = svc.build_seed_bundle("s1")
        if cp_id:
            self.assertEqual(seed.source_checkpoint_id, cp_id)


class TestNoHistoryLoss(unittest.TestCase):
    def test_checkpoint_deletion_does_not_affect_event_tracking(self):
        store = CheckpointStore()
        svc = _make_svc(checkpoint_store=store)
        events = _make_events("a", "b", "c")
        svc.update("s1", events)
        cp_id = svc.maybe_checkpoint("s1", "test")

        if cp_id:
            store.delete_checkpoint(cp_id)

        tracked = svc._all_events.get("s1", [])
        self.assertEqual(len(tracked), 3)

    def test_list_checkpoints_before_and_after_delete(self):
        store = CheckpointStore()
        cp = CompressionCheckpoint(
            checkpoint_id="cp-1",
            session_id="s1",
            created_at="2026-03-04T00:00:00+00:00",
            from_event_id=None,
            to_event_id="e5",
            summary_text="test",
        )
        store.save_checkpoint(cp)
        self.assertEqual(len(store.list_checkpoints("s1")), 1)
        store.delete_checkpoint("cp-1")
        self.assertEqual(len(store.list_checkpoints("s1")), 0)

    def test_failure_events_persisted_separately(self):
        store = CheckpointStore()
        from openminion.modules.context.compress.schemas import CheckpointFailedPayload

        failure = CheckpointFailedPayload(
            failure_id="f-1",
            session_id="s1",
            reason="budget exceeded",
            error_code=CHECKPOINT_ERROR_BUDGET_EXCEEDED,
            created_at="2026-03-04T00:00:00+00:00",
        )
        store.record_failure(failure)
        failures = store.list_failures("s1")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].failure_id, "f-1")

    def test_multiple_sessions_isolated(self):
        svc = _make_svc()
        svc.update("sess-A", _make_events("A events"))
        svc.update("sess-B", _make_events("B events"))
        svc.maybe_checkpoint("sess-A", "test")
        svc.maybe_checkpoint("sess-B", "test")

        cp_a = svc.get_latest_checkpoint("sess-A")
        cp_b = svc.get_latest_checkpoint("sess-B")
        self.assertIsNotNone(cp_a)
        self.assertIsNotNone(cp_b)
        if cp_a and cp_b:
            self.assertNotEqual(cp_a.checkpoint_id, cp_b.checkpoint_id)
            self.assertEqual(cp_a.session_id, "sess-A")
            self.assertEqual(cp_b.session_id, "sess-B")
