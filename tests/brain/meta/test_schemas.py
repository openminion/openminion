from __future__ import annotations

import unittest

from openminion.modules.brain.meta.schemas import (
    BudgetAdjust,
    MetaConfig,
    MetaDirective,
    MetaMetrics,
    MetaResult,
    MetaState,
    VerificationMode,
)


class TestMetaStateEnum(unittest.TestCase):
    def test_all_states_present(self) -> None:
        states = {s.value for s in MetaState}
        self.assertSetEqual(
            states,
            {"NORMAL", "CAUTIOUS", "HIGH_ASSURANCE", "RECOVERY", "PANIC"},
        )


class TestVerificationModeEnum(unittest.TestCase):
    def test_all_modes_present(self) -> None:
        modes = {m.value for m in VerificationMode}
        self.assertSetEqual(
            modes, {"none", "rule_based", "second_opinion", "panel_judge"}
        )


class TestMetaMetrics(unittest.TestCase):
    def test_defaults(self) -> None:
        m = MetaMetrics()
        self.assertEqual(m.risk_class, "low")
        self.assertEqual(m.risk_score, 0)
        self.assertAlmostEqual(m.intent_confidence, 0.7)
        self.assertAlmostEqual(m.grounding_confidence, 1.0)
        self.assertAlmostEqual(m.budget_remaining, 1.0)
        self.assertEqual(m.unknown_fields_count, 0)
        self.assertEqual(m.steps_completed_recent, 0)
        self.assertEqual(m.contradiction_flags, [])
        self.assertAlmostEqual(m.candidate_disagreement_score, 0.0)
        self.assertFalse(m.requires_evidence_only)
        self.assertEqual(m.tool_timeout_count_recent, 0)
        self.assertEqual(m.tool_auth_error_count_recent, 0)
        self.assertEqual(m.llm_calls_used, 0)
        self.assertEqual(m.llm_calls_max, 8)
        self.assertEqual(m.tool_calls_used, 0)
        self.assertEqual(m.tool_calls_max, 8)
        self.assertAlmostEqual(m.budget_pressure, 0.0)
        self.assertFalse(m.user_corrected_me_recently)
        self.assertFalse(m.user_requested_thoroughness)
        self.assertFalse(m.user_requested_brevity)
        self.assertFalse(m.user_kill_requested)
        self.assertFalse(m.needs_clarification)
        self.assertFalse(m.irreversible)

    def test_clamp_float_over(self) -> None:
        m = MetaMetrics(intent_confidence=9.0)
        self.assertAlmostEqual(m.intent_confidence, 1.0)

    def test_clamp_float_under(self) -> None:
        m = MetaMetrics(grounding_confidence=-5.0)
        self.assertAlmostEqual(m.grounding_confidence, 0.0)

    def test_clamp_risk_score_over(self) -> None:
        m = MetaMetrics(risk_score=999)
        self.assertEqual(m.risk_score, 100)

    def test_clamp_risk_score_under(self) -> None:
        m = MetaMetrics(risk_score=-10)
        self.assertEqual(m.risk_score, 0)

    def test_clamp_non_negative_int(self) -> None:
        m = MetaMetrics(recent_failures=-3)
        self.assertEqual(m.recent_failures, 0)
        m2 = MetaMetrics(unknown_fields_count=-2, llm_calls_used=-1)
        self.assertEqual(m2.unknown_fields_count, 0)
        self.assertEqual(m2.llm_calls_used, 0)

    def test_extended_fields_round_trip(self) -> None:
        metrics = MetaMetrics(
            unknown_fields_count=2,
            steps_completed_recent=4,
            contradiction_flags=["a", "b"],
            candidate_disagreement_score=0.61,
            requires_evidence_only=True,
            tool_timeout_count_recent=1,
            tool_auth_error_count_recent=1,
            llm_calls_used=3,
            llm_calls_max=9,
            tool_calls_used=2,
            tool_calls_max=7,
            budget_pressure=0.45,
            user_corrected_me_recently=True,
            user_requested_thoroughness=True,
            user_requested_brevity=False,
        )
        restored = MetaMetrics.model_validate(metrics.model_dump())
        self.assertEqual(restored.unknown_fields_count, 2)
        self.assertEqual(restored.steps_completed_recent, 4)
        self.assertEqual(restored.contradiction_flags, ["a", "b"])
        self.assertAlmostEqual(restored.candidate_disagreement_score, 0.61)
        self.assertTrue(restored.requires_evidence_only)
        self.assertEqual(restored.tool_timeout_count_recent, 1)
        self.assertEqual(restored.tool_auth_error_count_recent, 1)
        self.assertEqual(restored.llm_calls_used, 3)
        self.assertEqual(restored.llm_calls_max, 9)
        self.assertEqual(restored.tool_calls_used, 2)
        self.assertEqual(restored.tool_calls_max, 7)
        self.assertAlmostEqual(restored.budget_pressure, 0.45)
        self.assertTrue(restored.user_corrected_me_recently)
        self.assertTrue(restored.user_requested_thoroughness)
        self.assertFalse(restored.user_requested_brevity)

    def test_extra_field_forbidden(self) -> None:
        with self.assertRaises(Exception):
            MetaMetrics(unknown_extra_field="bad")  # type: ignore[call-arg]

    def test_risk_class_literal(self) -> None:
        with self.assertRaises(Exception):
            MetaMetrics(risk_class="catastrophic")  # type: ignore[arg-type]


class TestMetaDirective(unittest.TestCase):
    def test_defaults(self) -> None:
        d = MetaDirective()
        self.assertIsNone(d.override_next_state)
        self.assertIsNone(d.tier_override)
        self.assertFalse(d.require_confirmation)
        self.assertFalse(d.require_verification)
        self.assertEqual(d.verification_mode, VerificationMode.none)
        self.assertEqual(d.tool_temp_denylist, [])
        self.assertEqual(d.prompt_constraints, [])

    def test_extra_field_forbidden(self) -> None:
        with self.assertRaises(Exception):
            MetaDirective(not_a_field=True)  # type: ignore[call-arg]

    def test_override_next_state_literal(self) -> None:
        d = MetaDirective(override_next_state="STOPPED")
        self.assertEqual(d.override_next_state, "STOPPED")
        with self.assertRaises(Exception):
            MetaDirective(override_next_state="INVALID")  # type: ignore[arg-type]


class TestBudgetAdjust(unittest.TestCase):
    def test_defaults(self) -> None:
        b = BudgetAdjust()
        self.assertFalse(b.lower_context_limits)
        self.assertFalse(b.raise_context_limits)
        self.assertIsNone(b.lower_llm_calls_max)

    def test_extra_forbidden(self) -> None:
        with self.assertRaises(Exception):
            BudgetAdjust(unknown=True)  # type: ignore[call-arg]


class TestMetaResult(unittest.TestCase):
    def test_round_trip_serialization(self) -> None:
        metrics = MetaMetrics(risk_class="high", risk_score=80)
        directive = MetaDirective(
            require_verification=True, tier_override="T3_high_assurance"
        )
        result = MetaResult(
            meta_state=MetaState.HIGH_ASSURANCE,
            directive=directive,
            metrics=metrics,
            reasons=["HIGH_ASSURANCE_RISK_CLASS"],
            ruleset_version="metactl.v1",
        )
        data = result.model_dump()
        restored = MetaResult.model_validate(data)
        self.assertEqual(restored.meta_state, MetaState.HIGH_ASSURANCE)
        self.assertTrue(restored.directive.require_verification)
        self.assertEqual(restored.metrics.risk_score, 80)
        self.assertIn("HIGH_ASSURANCE_RISK_CLASS", restored.reasons)

    def test_ruleset_version_default(self) -> None:
        result = MetaResult(
            meta_state=MetaState.NORMAL,
            directive=MetaDirective(),
            metrics=MetaMetrics(),
        )
        self.assertEqual(result.ruleset_version, "metactl.v1")


class TestMetaConfig(unittest.TestCase):
    def test_defaults(self) -> None:
        cfg = MetaConfig()
        self.assertEqual(cfg.ruleset_version, "metactl.v1")
        self.assertEqual(cfg.high_risk_score_threshold, 70)
        self.assertAlmostEqual(cfg.low_grounding_threshold, 0.5)
        self.assertEqual(cfg.repeat_failure_threshold, 2)

    def test_extra_forbidden(self) -> None:
        with self.assertRaises(Exception):
            MetaConfig(bad_field=True)  # type: ignore[call-arg]
