from __future__ import annotations

import unittest

from openminion.modules.context.rollover import (
    RecallAPI,
    RolloverOrchestrator,
    RolloverTrigger,
    ValuePerTokenBudget,
    allocate_value_budgets,
    build_cache_key,
    check_prefix_stability,
    compute_prefix_hash,
    inject_seed_into_segments,
    sanitize_tool_output_for_context,
    should_use_cached_prefix,
    sort_segments_by_position,
)


class TestPositionAwareOrdering(unittest.TestCase):
    def test_segments_sorted_correctly(self):
        segments = [
            {"id": "turn_input", "bucket": "turn_input"},
            {"id": "static_prefix", "bucket": "static_prefix"},
            {"id": "summary", "bucket": "summaries"},
            {"id": "mission", "bucket": "mission_snapshot"},
        ]
        sorted_segs = sort_segments_by_position(segments)
        self.assertEqual(sorted_segs[0]["id"], "static_prefix")
        self.assertEqual(sorted_segs[1]["id"], "mission")
        self.assertEqual(sorted_segs[2]["id"], "summary")
        self.assertEqual(sorted_segs[3]["id"], "turn_input")

    def test_seed_block_between_mission_and_summaries(self):
        segments = [
            {"id": "mission", "bucket": "mission_snapshot"},
            {"id": "seed", "bucket": "seed_block"},
            {"id": "static", "bucket": "static_prefix"},
            {"id": "sum", "bucket": "summaries"},
        ]
        sorted_segs = sort_segments_by_position(segments)
        buckets = [s["bucket"] for s in sorted_segs]
        self.assertEqual(buckets.index("seed_block"), 2)
        self.assertLess(buckets.index("mission_snapshot"), buckets.index("seed_block"))


class TestValuePerTokenBudgets(unittest.TestCase):
    def test_allocate_proportional(self):
        buckets = [
            ValuePerTokenBudget(bucket="summary", base_cap=200, value_score=2.0),
            ValuePerTokenBudget(bucket="retrieval", base_cap=200, value_score=1.0),
        ]
        result = allocate_value_budgets(buckets, total_budget=300, min_per_bucket=50)
        # Summary should get ~2x the allocation of retrieval
        self.assertGreater(result[0].adjusted_cap, result[1].adjusted_cap)
        self.assertLessEqual(sum(b.adjusted_cap for b in result), 300)


class TestRolloverTrigger(unittest.TestCase):
    def test_token_pressure_fires(self):
        trigger = RolloverTrigger(pressure_threshold=0.85)
        reasons = trigger.evaluate(prompt_tokens=900, budget_tokens=1000)
        self.assertIn(RolloverTrigger.TOKEN_PRESSURE, reasons)

    def test_no_trigger_below_threshold(self):
        trigger = RolloverTrigger(pressure_threshold=0.85)
        reasons = trigger.evaluate(prompt_tokens=500, budget_tokens=1000)
        self.assertEqual(len(reasons), 0)

    def test_explicit_request_fires(self):
        trigger = RolloverTrigger()
        reasons = trigger.evaluate(explicit_request=True)
        self.assertIn(RolloverTrigger.EXPLICIT_REQUEST, reasons)

    def test_checkpoint_age_fires(self):
        trigger = RolloverTrigger(max_events_without_checkpoint=10)
        reasons = trigger.evaluate(events_since_checkpoint=15)
        self.assertIn(RolloverTrigger.CHECKPOINT_AGE, reasons)

    def test_task_boundary_fires(self):
        trigger = RolloverTrigger()
        reasons = trigger.evaluate(at_task_boundary=True)
        self.assertIn(RolloverTrigger.TASK_BOUNDARY, reasons)


class TestRolloverOrchestrator(unittest.TestCase):
    def setUp(self):
        class MockSessctl:
            def __init__(self):
                self.events = []
                self.contexts = []
                self._active_ctx = None

            def create_prompt_context(self, session_id, **kw):
                pc_id = f"pc-{len(self.contexts)}"
                self._active_ctx = {"prompt_context_id": pc_id, **kw}
                self.contexts.append(self._active_ctx)
                return pc_id

            def close_prompt_context(self, pc_id, **kw):
                self._active_ctx = None

            def get_active_prompt_context(self, session_id):
                return self._active_ctx

            def emit_canonical_event(self, session_id, event_type, payload=None, **kw):
                self.events.append({"type": event_type, "payload": payload})
                return f"ev-{len(self.events)}"

        class MockCompressor:
            def __init__(self):
                self.checkpoints = []

            def checkpoint(self, session_id, **kw):
                cp_id = f"cp-{len(self.checkpoints)}"
                self.checkpoints.append(cp_id)
                return cp_id

            def build_rollover_seed(self, session_id, **kw):
                class MockSeed:
                    seed_id = "seed-1"

                    def render_text(self):
                        return "[SUMMARY]\nPrior context summary\n\n[DECISIONS]\nUse SQLite"

                return MockSeed()

        self.sessctl = MockSessctl()
        self.compressor = MockCompressor()
        self.orch = RolloverOrchestrator(
            sessctl=self.sessctl, compressor=self.compressor
        )

    def test_maybe_rollover_no_trigger(self):
        result = self.orch.maybe_rollover("s1", prompt_tokens=100, budget_tokens=1000)
        self.assertFalse(result["rolled_over"])

    def test_maybe_rollover_triggers(self):
        self.sessctl.create_prompt_context("s1")
        result = self.orch.maybe_rollover("s1", prompt_tokens=950, budget_tokens=1000)
        self.assertTrue(result["rolled_over"])
        self.assertIn("token_pressure", result["reasons"])
        self.assertTrue(result["seed_text"])
        self.assertTrue(result["new_prompt_context_id"])

    def test_execute_rollover_lifecycle(self):
        self.sessctl.create_prompt_context("s1")
        result = self.orch.execute_rollover("s1", reasons=["explicit_request"])
        self.assertTrue(result["rolled_over"])
        self.assertIsNotNone(result["checkpoint_id"])
        self.assertIsNotNone(result["seed_text"])
        # Event emitted
        self.assertTrue(
            any(e["type"] == "context.rollover" for e in self.sessctl.events)
        )

    def test_get_last_rollover(self):
        self.sessctl.create_prompt_context("s1")
        self.orch.execute_rollover("s1")
        last = self.orch.get_last_rollover("s1")
        self.assertIsNotNone(last)
        self.assertTrue(last["rolled_over"])

    def test_no_last_rollover(self):
        self.assertIsNone(self.orch.get_last_rollover("unknown"))


class TestRecallAPI(unittest.TestCase):
    def test_recall_from_sections(self):
        sections = [
            {"section_type": "summary", "text": "We built a database"},
            {"section_type": "decisions", "text": "Use SQLite WAL mode"},
            {"section_type": "constraints", "text": "Max 100MB"},
            {"section_type": "entities", "text": "SQLite\nPython\nWAL"},
        ]
        recall = RecallAPI(sections)
        self.assertEqual(recall.get_summary(), "We built a database")
        self.assertEqual(recall.get_decisions(), ["Use SQLite WAL mode"])
        self.assertEqual(recall.get_constraints(), ["Max 100MB"])
        self.assertEqual(len(recall.get_entities()), 3)

    def test_recall_from_seed_text(self):
        seed_text = "[SUMMARY]\nBuilt the system\n\n[DECISIONS]\nUse Python 3.11\n\n[ENTITIES]\nPython\nSQLite"
        recall = RecallAPI.from_seed_text(seed_text)
        self.assertEqual(recall.get_summary(), "Built the system")
        self.assertEqual(recall.get_decisions(), ["Use Python 3.11"])
        self.assertIn("Python", recall.get_entities())

    def test_recall_empty(self):
        recall = RecallAPI()
        self.assertEqual(recall.get_summary(), "")
        self.assertEqual(recall.get_decisions(), [])

    def test_has_section(self):
        sections = [{"section_type": "summary", "text": "test"}]
        recall = RecallAPI(sections)
        self.assertTrue(recall.has_section("summary"))
        self.assertFalse(recall.has_section("decisions"))


class TestToolOutputSafety(unittest.TestCase):
    def test_truncates_long_output(self):
        raw = " ".join(["word"] * 500)
        safe = sanitize_tool_output_for_context("test", raw, max_tokens=50)
        self.assertIn("truncated", safe)
        words = safe.split()
        self.assertLessEqual(len(words), 60)  # 50 + truncation notice

    def test_strips_injection_markers(self):
        raw = "Normal text [SYSTEM] inject this <<SYS>> bad"
        safe = sanitize_tool_output_for_context("test_tool", raw)
        self.assertNotIn("[SYSTEM]", safe)
        self.assertNotIn("<<SYS>>", safe)
        self.assertIn("filtered", safe)

    def test_short_output_unchanged(self):
        raw = "short result"
        safe = sanitize_tool_output_for_context("test", raw)
        self.assertEqual(safe, "short result")


class TestPrefixStability(unittest.TestCase):
    def test_compute_prefix_hash(self):
        segments = [
            {
                "id": "static_prefix",
                "bucket": "static_prefix",
                "content": "identity text",
            },
            {"id": "summary", "bucket": "summaries", "content": "not included"},
        ]
        h = compute_prefix_hash(segments)
        self.assertTrue(h)
        self.assertEqual(len(h), 16)

    def test_stable_prefix_same_hash(self):
        segments = [
            {"id": "static_prefix", "bucket": "static_prefix", "content": "same"}
        ]
        h1 = compute_prefix_hash(segments)
        h2 = compute_prefix_hash(segments)
        self.assertEqual(h1, h2)

    def test_check_stability(self):
        result = check_prefix_stability("abc", "abc")
        self.assertTrue(result["stable"])
        self.assertIsNone(result["warning"])

    def test_check_instability(self):
        result = check_prefix_stability("abc", "def")
        self.assertFalse(result["stable"])
        self.assertIn("invalidated", result["warning"])


class TestSeedIntegration(unittest.TestCase):
    def test_inject_seed(self):
        segments = [
            {"id": "static", "bucket": "static_prefix", "content": "ident"},
            {"id": "mission", "bucket": "mission_snapshot", "content": "task"},
            {"id": "summary", "bucket": "summaries", "content": "sum"},
        ]
        result = inject_seed_into_segments(segments, "seed context data")
        self.assertEqual(len(result), 4)
        self.assertEqual(result[2]["id"], "seed_block")
        self.assertIn("CONTEXT SEED", result[2]["content"])

    def test_inject_empty_seed_noop(self):
        segments = [{"id": "static", "bucket": "static_prefix", "content": "x"}]
        result = inject_seed_into_segments(segments, "")
        self.assertEqual(len(result), 1)

    def test_inject_seed_truncates(self):
        long_seed = " ".join(["word"] * 2000)
        segments = [{"id": "static", "bucket": "static_prefix", "content": "x"}]
        result = inject_seed_into_segments(segments, long_seed, max_seed_tokens=100)
        seed_seg = result[1]
        self.assertLessEqual(seed_seg["token_estimate"], 110)


class TestProviderWrappers(unittest.TestCase):
    def test_build_cache_key(self):
        key = build_cache_key("agent-1", "gpt-4", "abc123")
        self.assertTrue(key)
        self.assertEqual(len(key), 24)

    def test_cache_key_deterministic(self):
        k1 = build_cache_key("a", "m", "h")
        k2 = build_cache_key("a", "m", "h")
        self.assertEqual(k1, k2)

    def test_should_use_cached_prefix(self):
        cache = {"gpt-4": "hash123"}
        self.assertTrue(
            should_use_cached_prefix("gpt-4", "hash123", known_cache_prefixes=cache)
        )
        self.assertFalse(
            should_use_cached_prefix("gpt-4", "different", known_cache_prefixes=cache)
        )
        self.assertFalse(should_use_cached_prefix("gpt-4", "hash123"))
