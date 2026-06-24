from __future__ import annotations

import unittest

from openminion.modules.brain.meta.evaluator import MetaRulesEngine
from openminion.modules.brain.meta.reasons import ReasonCode
from openminion.modules.brain.meta.schemas import (
    MetaConfig,
    MetaMetrics,
    MetaState,
    VerificationMode,
)


def _engine(cfg: MetaConfig | None = None) -> MetaRulesEngine:
    return MetaRulesEngine(cfg)


class TestDeterminism(unittest.TestCase):
    def _assert_deterministic(self, metrics: MetaMetrics, n: int = 5) -> None:
        engine = _engine()
        results = [engine.evaluate(metrics) for _ in range(n)]
        first = results[0]
        for r in results[1:]:
            self.assertEqual(r.meta_state, first.meta_state)
            self.assertEqual(r.directive.model_dump(), first.directive.model_dump())
            self.assertEqual(r.reasons, first.reasons)
            self.assertEqual(r.ruleset_version, first.ruleset_version)

    def test_normal_is_deterministic(self) -> None:
        self._assert_deterministic(MetaMetrics())

    def test_panic_is_deterministic(self) -> None:
        self._assert_deterministic(MetaMetrics(user_kill_requested=True))

    def test_high_assurance_is_deterministic(self) -> None:
        self._assert_deterministic(MetaMetrics(risk_class="high", risk_score=85))

    def test_recovery_is_deterministic(self) -> None:
        self._assert_deterministic(MetaMetrics(recent_failures=5))

    def test_cautious_is_deterministic(self) -> None:
        self._assert_deterministic(MetaMetrics(risk_class="medium"))


class TestPanicRule(unittest.TestCase):
    def test_user_kill_triggers_panic(self) -> None:
        result = _engine().evaluate(MetaMetrics(user_kill_requested=True))
        self.assertEqual(result.meta_state, MetaState.PANIC)

    def test_panic_overrides_state_to_stopped(self) -> None:
        result = _engine().evaluate(MetaMetrics(user_kill_requested=True))
        self.assertEqual(result.directive.override_next_state, "STOPPED")

    def test_panic_denies_all_tools(self) -> None:
        result = _engine().evaluate(MetaMetrics(user_kill_requested=True))
        self.assertIn("*", result.directive.tool_temp_denylist)

    def test_panic_reason_code(self) -> None:
        result = _engine().evaluate(MetaMetrics(user_kill_requested=True))
        self.assertIn(ReasonCode.PANIC_USER_KILL.value, result.reasons)

    def test_panic_overrides_recovery(self) -> None:
        result = _engine().evaluate(
            MetaMetrics(user_kill_requested=True, recent_failures=10, loop_count=10)
        )
        self.assertEqual(result.meta_state, MetaState.PANIC)


class TestRecoveryRule(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = MetaConfig(
            repeat_failure_threshold=2, loop_count_threshold=3, replan_count_threshold=3
        )

    def test_repeat_errors_triggers_recovery(self) -> None:
        result = _engine(self.cfg).evaluate(MetaMetrics(recent_failures=2))
        self.assertEqual(result.meta_state, MetaState.RECOVERY)

    def test_loop_triggers_recovery(self) -> None:
        result = _engine(self.cfg).evaluate(MetaMetrics(loop_count=3))
        self.assertEqual(result.meta_state, MetaState.RECOVERY)

    def test_replan_overrun_triggers_recovery(self) -> None:
        result = _engine(self.cfg).evaluate(MetaMetrics(replan_count=3))
        self.assertEqual(result.meta_state, MetaState.RECOVERY)

    def test_recovery_redirects_to_plan(self) -> None:
        result = _engine(self.cfg).evaluate(MetaMetrics(recent_failures=3))
        self.assertEqual(result.directive.override_next_state, "PLAN")

    def test_recovery_reason_codes(self) -> None:
        result = _engine(self.cfg).evaluate(
            MetaMetrics(recent_failures=3, loop_count=4)
        )
        self.assertIn(ReasonCode.RECOVERY_REPEAT_ERROR.value, result.reasons)
        self.assertIn(ReasonCode.RECOVERY_LOOP.value, result.reasons)

    def test_stall_triggers_recovery_reason(self) -> None:
        result = _engine(self.cfg).evaluate(
            MetaMetrics(
                ticks_without_progress=3,
                no_new_facts_streak=2,
            )
        )
        self.assertEqual(result.meta_state, MetaState.RECOVERY)
        self.assertIn(ReasonCode.RECOVERY_STALL.value, result.reasons)

    def test_recovery_adds_prompt_constraint(self) -> None:
        result = _engine(self.cfg).evaluate(MetaMetrics(recent_failures=2))
        self.assertTrue(result.directive.prompt_constraints)

    def test_below_threshold_no_recovery(self) -> None:
        result = _engine(self.cfg).evaluate(MetaMetrics(recent_failures=1))
        self.assertNotEqual(result.meta_state, MetaState.RECOVERY)


class TestHighAssuranceRule(unittest.TestCase):
    def test_high_risk_class_triggers_ha(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_class="high"))
        self.assertEqual(result.meta_state, MetaState.HIGH_ASSURANCE)

    def test_high_risk_score_triggers_ha(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_score=75))
        self.assertEqual(result.meta_state, MetaState.HIGH_ASSURANCE)

    def test_irreversible_triggers_ha(self) -> None:
        result = _engine().evaluate(MetaMetrics(irreversible=True))
        self.assertEqual(result.meta_state, MetaState.HIGH_ASSURANCE)

    def test_low_grounding_triggers_ha(self) -> None:
        result = _engine().evaluate(MetaMetrics(grounding_confidence=0.3))
        self.assertEqual(result.meta_state, MetaState.HIGH_ASSURANCE)

    def test_failed_verify_triggers_ha(self) -> None:
        result = _engine().evaluate(MetaMetrics(last_verify_outcome="fail"))
        self.assertEqual(result.meta_state, MetaState.HIGH_ASSURANCE)

    def test_ha_requires_verification(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_class="high"))
        self.assertTrue(result.directive.require_verification)

    def test_ha_tier_override(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_class="high"))
        self.assertEqual(result.directive.tier_override, "T3_high_assurance")

    def test_ha_with_side_effects_requires_confirmation(self) -> None:
        result = _engine().evaluate(
            MetaMetrics(risk_class="high", requires_side_effects=True)
        )
        self.assertTrue(result.directive.require_confirmation)

    def test_ha_without_side_effects_no_confirmation(self) -> None:
        result = _engine().evaluate(
            MetaMetrics(risk_class="high", requires_side_effects=False)
        )
        self.assertFalse(result.directive.require_confirmation)

    def test_ha_verification_mode(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_class="high"))
        self.assertEqual(
            result.directive.verification_mode, VerificationMode.panel_judge
        )

    def test_ha_reason_codes(self) -> None:
        result = _engine().evaluate(
            MetaMetrics(risk_class="high", risk_score=80, irreversible=True)
        )
        self.assertIn(ReasonCode.HIGH_ASSURANCE_RISK_CLASS.value, result.reasons)
        self.assertIn(ReasonCode.HIGH_ASSURANCE_RISK_SCORE.value, result.reasons)
        self.assertIn(ReasonCode.HIGH_ASSURANCE_IRREVERSIBLE.value, result.reasons)

    def test_candidate_disagreement_boundary_does_not_trigger_at_0_6(self) -> None:
        result = _engine().evaluate(MetaMetrics(candidate_disagreement_score=0.6))
        self.assertNotEqual(result.meta_state, MetaState.HIGH_ASSURANCE)
        self.assertNotIn(
            ReasonCode.HIGH_ASSURANCE_CANDIDATE_DISAGREEMENT.value, result.reasons
        )

    def test_candidate_disagreement_triggers_above_0_6(self) -> None:
        result = _engine().evaluate(MetaMetrics(candidate_disagreement_score=0.6001))
        self.assertEqual(result.meta_state, MetaState.HIGH_ASSURANCE)
        self.assertIn(
            ReasonCode.HIGH_ASSURANCE_CANDIDATE_DISAGREEMENT.value, result.reasons
        )


class TestCautiousRule(unittest.TestCase):
    def test_medium_risk_class_triggers_cautious(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_class="medium"))
        self.assertEqual(result.meta_state, MetaState.CAUTIOUS)

    def test_needs_clarification_triggers_cautious(self) -> None:
        result = _engine().evaluate(MetaMetrics(needs_clarification=True))
        self.assertEqual(result.meta_state, MetaState.CAUTIOUS)

    def test_needs_clarification_overrides_to_waiting(self) -> None:
        result = _engine().evaluate(MetaMetrics(needs_clarification=True))
        self.assertEqual(result.directive.override_next_state, "WAITING")

    def test_needs_clarification_sets_require_clarification_flag(self) -> None:
        result = _engine().evaluate(MetaMetrics(needs_clarification=True))
        self.assertTrue(result.directive.require_clarification)

    def test_needs_clarification_sets_clarification_question(self) -> None:
        result = _engine().evaluate(MetaMetrics(needs_clarification=True))
        self.assertIsNotNone(result.directive.clarification_question)
        self.assertTrue(str(result.directive.clarification_question).strip())

    def test_needs_clarification_sets_escalation_question(self) -> None:
        result = _engine().evaluate(MetaMetrics(needs_clarification=True))
        self.assertIsNotNone(result.directive.escalation_question)

    def test_low_intent_confidence_triggers_cautious(self) -> None:
        result = _engine().evaluate(MetaMetrics(intent_confidence=0.4))
        self.assertEqual(result.meta_state, MetaState.CAUTIOUS)

    def test_policy_denies_triggers_cautious(self) -> None:
        result = _engine().evaluate(MetaMetrics(policy_recent_denies=2))
        self.assertEqual(result.meta_state, MetaState.CAUTIOUS)

    def test_tool_degraded_triggers_cautious(self) -> None:
        result = _engine().evaluate(MetaMetrics(tool_success_rate_ewma=0.5))
        self.assertEqual(result.meta_state, MetaState.CAUTIOUS)
        self.assertTrue(result.directive.prompt_constraints)

    def test_budget_pressure_triggers_cautious(self) -> None:
        # budget_remaining=0.1 → pressure=0.9 > threshold=0.8
        result = _engine().evaluate(MetaMetrics(budget_remaining=0.1))
        self.assertEqual(result.meta_state, MetaState.CAUTIOUS)
        self.assertIsNotNone(result.directive.budget_adjustments)

    def test_cautious_tier_override(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_class="medium"))
        self.assertEqual(result.directive.tier_override, "T1_light")

    def test_cautious_reason_codes(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_class="medium"))
        self.assertIn(ReasonCode.CAUTIOUS_MEDIUM_RISK_CLASS.value, result.reasons)

    def test_cautious_budget_reason_code(self) -> None:
        result = _engine().evaluate(MetaMetrics(budget_remaining=0.1))
        self.assertIn(ReasonCode.CAUTIOUS_BUDGET_PRESSURE.value, result.reasons)


class TestNormalRule(unittest.TestCase):
    def test_defaults_produce_normal(self) -> None:
        result = _engine().evaluate(MetaMetrics())
        self.assertEqual(result.meta_state, MetaState.NORMAL)

    def test_normal_empty_directive(self) -> None:
        result = _engine().evaluate(MetaMetrics())
        self.assertIsNone(result.directive.override_next_state)
        self.assertIsNone(result.directive.tier_override)
        self.assertFalse(result.directive.require_confirmation)
        self.assertFalse(result.directive.require_verification)

    def test_normal_reason_code(self) -> None:
        result = _engine().evaluate(MetaMetrics())
        self.assertIn(ReasonCode.NORMAL_DEFAULT.value, result.reasons)

    def test_ruleset_version_in_result(self) -> None:
        result = _engine().evaluate(MetaMetrics())
        self.assertEqual(result.ruleset_version, "metactl.v1")

    def test_custom_ruleset_version(self) -> None:
        cfg = MetaConfig(ruleset_version="metactl.v2-test")
        result = _engine(cfg).evaluate(MetaMetrics())
        self.assertEqual(result.ruleset_version, "metactl.v2-test")
