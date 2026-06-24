from __future__ import annotations

import unittest

from openminion.modules.context.compress.compaction import (
    BudgetArbiter,
    CompactionService,
    TriggerPolicy,
    TriggerReason,
    distill_tool_output,
    exclude_raw_tool_output,
)
from openminion.modules.context.compress.schemas import (
    SeedBundle,
    SeedBundleBudgets,
    SeedSection,
    TierEntry,
)
from openminion.modules.context.compress.strategies import (
    DeltaEvent,
    DecisionStrategy,
    EntityStrategy,
    StrategyRegistry,
    SummaryStrategy,
    ToolDigestStrategy,
)


class TestStrategyExtract(unittest.TestCase):
    def test_summary_extract_from_dialogue(self):
        s = SummaryStrategy()
        events = [
            DeltaEvent(event_id="e1", event_type="turn.user", text="Hello world"),
            DeltaEvent(event_id="e2", event_type="turn.assistant", text="Hi there"),
        ]
        entries = s.extract(events)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].tier_type, "summary")
        self.assertIn("Hello world", entries[0].text)

    def test_summary_extract_ignores_non_dialogue(self):
        s = SummaryStrategy()
        events = [DeltaEvent(event_id="e1", event_type="tool.completed", text="result")]
        entries = s.extract(events)
        self.assertEqual(len(entries), 0)

    def test_decision_extract(self):
        s = DecisionStrategy()
        events = [
            DeltaEvent(
                event_id="e1",
                event_type="decision.made",
                payload={"decision_text": "Use SQLite"},
                text="",
            ),
            DeltaEvent(
                event_id="e2",
                event_type="constraint.set",
                payload={"constraint_text": "Max 1000 tokens"},
            ),
        ]
        entries = s.extract(events)
        decisions = [e for e in entries if e.tier_type == "decisions"]
        constraints = [e for e in entries if e.tier_type == "constraints"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].text, "Use SQLite")
        self.assertEqual(len(constraints), 1)
        self.assertIn("1000 tokens", constraints[0].text)

    def test_entity_extract(self):
        s = EntityStrategy()
        events = [
            DeltaEvent(
                event_id="e1",
                event_type="turn.user",
                payload={"entities": ["Python", "SQLite"]},
            ),
        ]
        entries = s.extract(events)
        self.assertEqual(len(entries), 2)

    def test_tool_digest_extract(self):
        s = ToolDigestStrategy()
        events = [
            DeltaEvent(
                event_id="e1",
                event_type="tool.completed",
                payload={"tool_name": "search", "summary": "Found 3 results"},
            ),
        ]
        entries = s.extract(events)
        self.assertEqual(len(entries), 1)
        self.assertIn("search", entries[0].text)


class TestStrategyAbstract(unittest.TestCase):
    def test_summary_abstract_merges(self):
        s = SummaryStrategy()
        old = [TierEntry(tier_type="summary", text="Hello world", token_count=2)]
        new = [TierEntry(tier_type="summary", text="New information", token_count=2)]
        merged = s.abstract(old, new, token_budget=100)
        self.assertEqual(len(merged), 1)
        self.assertIn("Hello", merged[0].text)
        self.assertIn("New", merged[0].text)

    def test_summary_abstract_respects_budget(self):
        s = SummaryStrategy()
        old = [
            TierEntry(tier_type="summary", text=" ".join(["word"] * 50), token_count=50)
        ]
        new = [
            TierEntry(
                tier_type="summary", text=" ".join(["extra"] * 50), token_count=50
            )
        ]
        merged = s.abstract(old, new, token_budget=10)
        self.assertLessEqual(merged[0].token_count, 10)

    def test_decision_abstract_deduplicates(self):
        s = DecisionStrategy()
        old = [TierEntry(tier_type="decisions", text="Use SQLite", token_count=2)]
        new = [TierEntry(tier_type="decisions", text="Use SQLite", token_count=2)]
        merged = s.abstract(old, new, token_budget=100)
        self.assertEqual(len(merged), 1)

    def test_entity_abstract_deduplicates_case_insensitive(self):
        s = EntityStrategy()
        old = [TierEntry(tier_type="entities", text="Python", token_count=1)]
        new = [TierEntry(tier_type="entities", text="python", token_count=1)]
        merged = s.abstract(old, new, token_budget=100)
        self.assertEqual(len(merged), 1)


class TestStrategyRegistry(unittest.TestCase):
    def test_default_registry_has_all_strategies(self):
        reg = StrategyRegistry.default()
        self.assertIsNotNone(reg.get("summary"))
        self.assertIsNotNone(reg.get("decisions"))
        self.assertIsNotNone(reg.get("entities"))
        self.assertIsNotNone(reg.get("tool_digests"))

    def test_all_strategies_deduplicates(self):
        reg = StrategyRegistry.default()
        strats = reg.all_strategies()
        # DecisionStrategy handles both "decisions" and "constraints"
        # V1.6 adds OpenLoopStrategy = 5 total
        self.assertEqual(len(strats), 5)


class TestCompactionServiceUpdate(unittest.TestCase):
    def setUp(self):
        self.svc = CompactionService()

    def test_update_empty_events_returns_empty_bundle(self):
        bundle = self.svc.update("s1", [])
        self.assertEqual(bundle.session_id, "s1")
        self.assertEqual(bundle.total_tokens, 0)

    def test_update_dialogue_populates_summary(self):
        events = [
            DeltaEvent(event_id="e1", event_type="turn.user", text="Hello world"),
            DeltaEvent(
                event_id="e2", event_type="turn.assistant", text="Hi there friend"
            ),
        ]
        bundle = self.svc.update("s1", events)
        self.assertTrue(bundle.summary_text.strip())
        self.assertEqual(bundle.up_to_event_id, "e2")
        self.assertGreater(bundle.total_tokens, 0)

    def test_update_increments_version(self):
        events = [DeltaEvent(event_id="e1", event_type="turn.user", text="Hello")]
        b1 = self.svc.update("s1", events)
        v1 = b1.version
        events2 = [DeltaEvent(event_id="e2", event_type="turn.user", text="World")]
        b2 = self.svc.update("s1", events2)
        self.assertEqual(b2.version, v1 + 1)

    def test_update_with_decisions(self):
        events = [
            DeltaEvent(
                event_id="e1",
                event_type="decision.made",
                payload={"decision_text": "Use Python 3.11"},
            ),
        ]
        bundle = self.svc.update("s1", events)
        decisions = [t for t in bundle.tiers if t.tier_type == "decisions"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].text, "Use Python 3.11")

    def test_update_strips_raw_tool_output(self):
        events = [
            DeltaEvent(
                event_id="e1",
                event_type="tool.completed",
                payload={"tool_name": "read_file", "output": "x " * 200},
                text="x " * 200,
            ),
        ]
        bundle = self.svc.update("s1", events)
        # Tool digests should exist, but with truncated output
        digests = [t for t in bundle.tiers if t.tier_type == "tool_digests"]
        if digests:
            self.assertLessEqual(digests[0].token_count, 110)


class TestCompactionServiceCheckpoint(unittest.TestCase):
    def setUp(self):
        self.svc = CompactionService()

    def test_checkpoint_returns_id(self):
        self.svc.update(
            "s1",
            [
                DeltaEvent(event_id="e1", event_type="turn.user", text="Hello"),
            ],
        )
        cp_id = self.svc.checkpoint("s1")
        self.assertTrue(cp_id)
        bundle = self.svc.get_latest("s1")
        self.assertEqual(bundle.checkpoint_id, cp_id)

    def test_checkpoint_preserves_bundle_state(self):
        events = [
            DeltaEvent(event_id="e1", event_type="turn.user", text="Important context"),
            DeltaEvent(
                event_id="e2",
                event_type="decision.made",
                payload={"decision_text": "Keep this decision"},
            ),
        ]
        self.svc.update("s1", events)
        self.svc.checkpoint("s1")
        bundle = self.svc.get_latest("s1")
        self.assertTrue(bundle.summary_text.strip())
        decisions = [t for t in bundle.tiers if t.tier_type == "decisions"]
        self.assertEqual(len(decisions), 1)


class TestCompactionServiceGetLatest(unittest.TestCase):
    def test_get_latest_creates_empty_if_none(self):
        svc = CompactionService()
        bundle = svc.get_latest("new-session")
        self.assertEqual(bundle.session_id, "new-session")
        self.assertEqual(bundle.summary_text, "")
        self.assertEqual(len(bundle.tiers), 0)


class TestBuildRolloverSeed(unittest.TestCase):
    def setUp(self):
        self.svc = CompactionService()
        events = [
            DeltaEvent(
                event_id="e1",
                event_type="turn.user",
                text="Working on database migration",
            ),
            DeltaEvent(
                event_id="e2",
                event_type="turn.assistant",
                text="I will use SQLite for the migration",
            ),
            DeltaEvent(
                event_id="e3",
                event_type="decision.made",
                payload={"decision_text": "Use SQLite WAL mode"},
            ),
            DeltaEvent(
                event_id="e4",
                event_type="constraint.set",
                payload={"constraint_text": "Max 100MB database size"},
            ),
            DeltaEvent(
                event_id="e5",
                event_type="turn.user",
                payload={"entities": ["SQLite", "WAL", "migration"]},
            ),
            DeltaEvent(
                event_id="e6",
                event_type="tool.completed",
                payload={
                    "tool_name": "run_sql",
                    "summary": "Migration complete, 5 tables created",
                },
            ),
        ]
        self.svc.update("s1", events)

    def test_build_seed_has_sections(self):
        seed = self.svc.build_rollover_seed("s1")
        self.assertIsInstance(seed, SeedBundle)
        self.assertEqual(seed.session_id, "s1")
        self.assertGreater(len(seed.sections), 0)
        self.assertGreater(seed.total_tokens, 0)

    def test_build_seed_has_summary(self):
        seed = self.svc.build_rollover_seed("s1")
        summaries = [s for s in seed.sections if s.section_type == "summary"]
        self.assertEqual(len(summaries), 1)

    def test_build_seed_has_decisions(self):
        seed = self.svc.build_rollover_seed("s1")
        decisions = [s for s in seed.sections if s.section_type == "decisions"]
        self.assertEqual(len(decisions), 1)
        self.assertIn("SQLite WAL", decisions[0].text)

    def test_build_seed_render_text(self):
        seed = self.svc.build_rollover_seed("s1")
        text = seed.render_text()
        self.assertIn("[SUMMARY]", text)
        self.assertIn("[DECISIONS]", text)

    def test_build_seed_respects_budget(self):
        tiny_budgets = SeedBundleBudgets(total_max_tokens=20)
        seed = self.svc.build_rollover_seed("s1", budgets=tiny_budgets)
        self.assertLessEqual(seed.total_tokens, 20)

    def test_build_seed_from_checkpoint(self):
        cp_id = self.svc.checkpoint("s1")
        seed = self.svc.build_rollover_seed_from_checkpoint(cp_id)
        self.assertIsInstance(seed, SeedBundle)
        self.assertGreater(seed.total_tokens, 0)

    def test_build_seed_from_invalid_checkpoint_raises(self):
        with self.assertRaises(ValueError):
            self.svc.build_rollover_seed_from_checkpoint("nonexistent")


class TestToolDigestDistillation(unittest.TestCase):
    def test_distill_truncates(self):
        raw = " ".join(["word"] * 200)
        result = distill_tool_output("test_tool", raw, max_tokens=50)
        self.assertIn("test_tool", result["tool_name"])
        words = result["distilled_summary"].rstrip("...").split()
        self.assertLessEqual(len(words), 50)
        self.assertEqual(result["source"], "extractive")

    def test_distill_short_output_unchanged(self):
        raw = "short result"
        result = distill_tool_output("test_tool", raw)
        self.assertEqual(result["distilled_summary"], "short result")


class TestRawToolOutputExclusion(unittest.TestCase):
    def test_raw_output_stripped(self):
        events = [
            DeltaEvent(
                event_id="e1",
                event_type="tool.completed",
                payload={"tool_name": "read", "output": "very long raw output"},
                text="very long raw output",
            ),
            DeltaEvent(event_id="e2", event_type="turn.user", text="Hello"),
        ]
        cleaned = exclude_raw_tool_output(events)
        self.assertEqual(len(cleaned), 2)
        # Tool event should have distilled summary, not raw
        tool_ev = cleaned[0]
        self.assertNotIn("output", tool_ev.payload)
        self.assertIn("distilled_summary", tool_ev.payload)
        # Non-tool event unchanged
        self.assertEqual(cleaned[1].text, "Hello")


class TestTriggerPolicy(unittest.TestCase):
    def test_token_pressure_fires(self):
        policy = TriggerPolicy(token_pressure_threshold=0.8)
        events = [DeltaEvent(event_id="e1", event_type="turn.user", text="Hi")]
        reasons = policy.evaluate(
            events, None, estimated_prompt_tokens=900, budget_total_tokens=1000
        )
        self.assertIn(TriggerReason.TOKEN_PRESSURE, reasons)

    def test_no_trigger_below_threshold(self):
        policy = TriggerPolicy(token_pressure_threshold=0.8)
        events = [DeltaEvent(event_id="e1", event_type="turn.user", text="Hi")]
        reasons = policy.evaluate(
            events, None, estimated_prompt_tokens=500, budget_total_tokens=1000
        )
        self.assertNotIn(TriggerReason.TOKEN_PRESSURE, reasons)

    def test_large_tool_output_fires(self):
        policy = TriggerPolicy(large_tool_output_tokens=10)
        events = [
            DeltaEvent(
                event_id="e1", event_type="tool.completed", text=" ".join(["x"] * 20)
            ),
        ]
        reasons = policy.evaluate(events, None)
        self.assertIn(TriggerReason.LARGE_TOOL_OUTPUT, reasons)

    def test_manual_refresh_fires(self):
        policy = TriggerPolicy()
        reasons = policy.evaluate([], None, manual_refresh=True)
        self.assertIn(TriggerReason.MANUAL_REFRESH, reasons)

    def test_run_finished_fires(self):
        policy = TriggerPolicy()
        events = [DeltaEvent(event_id="e1", event_type="run.finished")]
        reasons = policy.evaluate(events, None)
        self.assertIn(TriggerReason.AFTER_RUN_FINISHED, reasons)


class TestBudgetArbiter(unittest.TestCase):
    def test_enforce_trims_to_budget(self):
        budgets = SeedBundleBudgets(total_max_tokens=10, summary_max_tokens=5)
        arbiter = BudgetArbiter(budgets)
        sections = [
            SeedSection(
                section_type="summary", text=" ".join(["w"] * 20), token_count=20
            ),
        ]
        trimmed = arbiter.enforce(sections)
        self.assertLessEqual(sum(s.token_count for s in trimmed), 10)

    def test_enforce_multi_section_total_cap(self):
        budgets = SeedBundleBudgets(
            total_max_tokens=10,
            summary_max_tokens=8,
            decisions_max_tokens=8,
        )
        arbiter = BudgetArbiter(budgets)
        sections = [
            SeedSection(
                section_type="summary", text=" ".join(["w"] * 8), token_count=8
            ),
            SeedSection(
                section_type="decisions", text=" ".join(["w"] * 8), token_count=8
            ),
        ]
        trimmed = arbiter.enforce(sections)
        total = sum(s.token_count for s in trimmed)
        self.assertLessEqual(total, 10)


class TestSessionIntegration(unittest.TestCase):
    def test_checkpoint_persists_to_sessctl(self):

        class MockSessctl:
            def __init__(self):
                self.checkpoints = []
                self.seeds = []

            def save_compression_checkpoint(self, session_id, bundle_json, **kw):
                self.checkpoints.append(
                    {
                        "session_id": session_id,
                        "bundle_json": bundle_json,
                        **kw,
                    }
                )
                return "cp-mock"

            def save_seed_bundle(
                self, session_id, source_bundle_id, sections_json, total_tokens, **kw
            ):
                self.seeds.append(
                    {
                        "session_id": session_id,
                        "total_tokens": total_tokens,
                        **kw,
                    }
                )
                return "seed-mock"

            def get_latest_checkpoint(self, session_id):
                return None

        mock = MockSessctl()
        svc = CompactionService(sessctl=mock)
        svc.update("s1", [DeltaEvent(event_id="e1", event_type="turn.user", text="Hi")])
        svc.checkpoint("s1", reason="test")
        self.assertEqual(len(mock.checkpoints), 1)
        self.assertEqual(mock.checkpoints[0]["session_id"], "s1")
        self.assertEqual(mock.checkpoints[0]["reason"], "test")

    def test_seed_persists_to_sessctl(self):
        class MockSessctl:
            def __init__(self):
                self.seeds = []

            def save_seed_bundle(
                self, session_id, source_bundle_id, sections_json, total_tokens, **kw
            ):
                self.seeds.append(
                    {"session_id": session_id, "total_tokens": total_tokens}
                )
                return "seed-mock"

            def get_latest_checkpoint(self, session_id):
                return None

        mock = MockSessctl()
        svc = CompactionService(sessctl=mock)
        svc.update(
            "s1",
            [
                DeltaEvent(event_id="e1", event_type="turn.user", text="Hello world"),
            ],
        )
        svc.build_rollover_seed("s1")
        self.assertEqual(len(mock.seeds), 1)
