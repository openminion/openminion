from __future__ import annotations

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


def _assert_deterministic(metrics: MetaMetrics, n: int = 5) -> None:
    engine = _engine()
    results = [engine.evaluate(metrics) for _ in range(n)]
    first = results[0]
    for result in results[1:]:
        assert result.meta_state == first.meta_state
        assert result.directive.model_dump() == first.directive.model_dump()
        assert result.reasons == first.reasons
        assert result.ruleset_version == first.ruleset_version


class TestDeterminism:
    def test_normal_is_deterministic(self) -> None:
        _assert_deterministic(MetaMetrics())

    def test_panic_is_deterministic(self) -> None:
        _assert_deterministic(MetaMetrics(user_kill_requested=True))

    def test_high_assurance_is_deterministic(self) -> None:
        _assert_deterministic(MetaMetrics(risk_class="high", risk_score=85))

    def test_recovery_is_deterministic(self) -> None:
        _assert_deterministic(MetaMetrics(recent_failures=5))

    def test_cautious_is_deterministic(self) -> None:
        _assert_deterministic(MetaMetrics(risk_class="medium"))


class TestPanicRule:
    def test_user_kill_triggers_panic(self) -> None:
        result = _engine().evaluate(MetaMetrics(user_kill_requested=True))
        assert result.meta_state == MetaState.PANIC

    def test_panic_overrides_state_to_stopped(self) -> None:
        result = _engine().evaluate(MetaMetrics(user_kill_requested=True))
        assert result.directive.override_next_state == "STOPPED"

    def test_panic_denies_all_tools(self) -> None:
        result = _engine().evaluate(MetaMetrics(user_kill_requested=True))
        assert "*" in result.directive.tool_temp_denylist

    def test_panic_reason_code(self) -> None:
        result = _engine().evaluate(MetaMetrics(user_kill_requested=True))
        assert ReasonCode.PANIC_USER_KILL.value in result.reasons

    def test_panic_overrides_recovery(self) -> None:
        result = _engine().evaluate(
            MetaMetrics(user_kill_requested=True, recent_failures=10, loop_count=10)
        )
        assert result.meta_state == MetaState.PANIC


class TestRecoveryRule:
    cfg = MetaConfig(
        repeat_failure_threshold=2,
        loop_count_threshold=3,
        replan_count_threshold=3,
    )

    def test_repeat_errors_triggers_recovery(self) -> None:
        result = _engine(self.cfg).evaluate(MetaMetrics(recent_failures=2))
        assert result.meta_state == MetaState.RECOVERY

    def test_loop_triggers_recovery(self) -> None:
        result = _engine(self.cfg).evaluate(MetaMetrics(loop_count=3))
        assert result.meta_state == MetaState.RECOVERY

    def test_replan_overrun_triggers_recovery(self) -> None:
        result = _engine(self.cfg).evaluate(MetaMetrics(replan_count=3))
        assert result.meta_state == MetaState.RECOVERY

    def test_recovery_redirects_to_plan(self) -> None:
        result = _engine(self.cfg).evaluate(MetaMetrics(recent_failures=3))
        assert result.directive.override_next_state == "PLAN"

    def test_recovery_reason_codes(self) -> None:
        result = _engine(self.cfg).evaluate(
            MetaMetrics(recent_failures=3, loop_count=4)
        )
        assert ReasonCode.RECOVERY_REPEAT_ERROR.value in result.reasons
        assert ReasonCode.RECOVERY_LOOP.value in result.reasons

    def test_stall_triggers_recovery_reason(self) -> None:
        result = _engine(self.cfg).evaluate(
            MetaMetrics(
                ticks_without_progress=3,
                no_new_facts_streak=2,
            )
        )
        assert result.meta_state == MetaState.RECOVERY
        assert ReasonCode.RECOVERY_STALL.value in result.reasons

    def test_recovery_adds_prompt_constraint(self) -> None:
        result = _engine(self.cfg).evaluate(MetaMetrics(recent_failures=2))
        assert result.directive.prompt_constraints

    def test_below_threshold_no_recovery(self) -> None:
        result = _engine(self.cfg).evaluate(MetaMetrics(recent_failures=1))
        assert result.meta_state != MetaState.RECOVERY


class TestHighAssuranceRule:
    def test_high_risk_class_triggers_ha(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_class="high"))
        assert result.meta_state == MetaState.HIGH_ASSURANCE

    def test_high_risk_score_triggers_ha(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_score=75))
        assert result.meta_state == MetaState.HIGH_ASSURANCE

    def test_irreversible_triggers_ha(self) -> None:
        result = _engine().evaluate(MetaMetrics(irreversible=True))
        assert result.meta_state == MetaState.HIGH_ASSURANCE

    def test_low_grounding_triggers_ha(self) -> None:
        result = _engine().evaluate(MetaMetrics(grounding_confidence=0.3))
        assert result.meta_state == MetaState.HIGH_ASSURANCE

    def test_failed_verify_triggers_ha(self) -> None:
        result = _engine().evaluate(MetaMetrics(last_verify_outcome="fail"))
        assert result.meta_state == MetaState.HIGH_ASSURANCE

    def test_ha_requires_verification(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_class="high"))
        assert result.directive.require_verification

    def test_ha_tier_override(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_class="high"))
        assert result.directive.tier_override == "T3_high_assurance"

    def test_ha_with_side_effects_requires_confirmation(self) -> None:
        result = _engine().evaluate(
            MetaMetrics(risk_class="high", requires_side_effects=True)
        )
        assert result.directive.require_confirmation

    def test_ha_without_side_effects_no_confirmation(self) -> None:
        result = _engine().evaluate(
            MetaMetrics(risk_class="high", requires_side_effects=False)
        )
        assert not result.directive.require_confirmation

    def test_ha_verification_mode(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_class="high"))
        assert result.directive.verification_mode == VerificationMode.panel_judge

    def test_ha_reason_codes(self) -> None:
        result = _engine().evaluate(
            MetaMetrics(risk_class="high", risk_score=80, irreversible=True)
        )
        assert ReasonCode.HIGH_ASSURANCE_RISK_CLASS.value in result.reasons
        assert ReasonCode.HIGH_ASSURANCE_RISK_SCORE.value in result.reasons
        assert ReasonCode.HIGH_ASSURANCE_IRREVERSIBLE.value in result.reasons

    def test_candidate_disagreement_boundary_does_not_trigger_at_0_6(self) -> None:
        result = _engine().evaluate(MetaMetrics(candidate_disagreement_score=0.6))
        assert result.meta_state != MetaState.HIGH_ASSURANCE
        assert (
            ReasonCode.HIGH_ASSURANCE_CANDIDATE_DISAGREEMENT.value
            not in result.reasons
        )

    def test_candidate_disagreement_triggers_above_0_6(self) -> None:
        result = _engine().evaluate(MetaMetrics(candidate_disagreement_score=0.6001))
        assert result.meta_state == MetaState.HIGH_ASSURANCE
        assert ReasonCode.HIGH_ASSURANCE_CANDIDATE_DISAGREEMENT.value in result.reasons


class TestCautiousRule:
    def test_medium_risk_class_triggers_cautious(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_class="medium"))
        assert result.meta_state == MetaState.CAUTIOUS

    def test_needs_clarification_triggers_cautious(self) -> None:
        result = _engine().evaluate(MetaMetrics(needs_clarification=True))
        assert result.meta_state == MetaState.CAUTIOUS

    def test_needs_clarification_overrides_to_waiting(self) -> None:
        result = _engine().evaluate(MetaMetrics(needs_clarification=True))
        assert result.directive.override_next_state == "WAITING"

    def test_needs_clarification_sets_require_clarification_flag(self) -> None:
        result = _engine().evaluate(MetaMetrics(needs_clarification=True))
        assert result.directive.require_clarification

    def test_needs_clarification_sets_clarification_question(self) -> None:
        result = _engine().evaluate(MetaMetrics(needs_clarification=True))
        assert result.directive.clarification_question is not None
        assert str(result.directive.clarification_question).strip()

    def test_needs_clarification_sets_escalation_question(self) -> None:
        result = _engine().evaluate(MetaMetrics(needs_clarification=True))
        assert result.directive.escalation_question is not None

    def test_low_intent_confidence_triggers_cautious(self) -> None:
        result = _engine().evaluate(MetaMetrics(intent_confidence=0.4))
        assert result.meta_state == MetaState.CAUTIOUS

    def test_policy_denies_triggers_cautious(self) -> None:
        result = _engine().evaluate(MetaMetrics(policy_recent_denies=2))
        assert result.meta_state == MetaState.CAUTIOUS

    def test_tool_degraded_triggers_cautious(self) -> None:
        result = _engine().evaluate(MetaMetrics(tool_success_rate_ewma=0.5))
        assert result.meta_state == MetaState.CAUTIOUS
        assert result.directive.prompt_constraints

    def test_budget_pressure_triggers_cautious(self) -> None:
        # budget_remaining=0.1 → pressure=0.9 > threshold=0.8
        result = _engine().evaluate(MetaMetrics(budget_remaining=0.1))
        assert result.meta_state == MetaState.CAUTIOUS
        assert result.directive.budget_adjustments is not None

    def test_cautious_tier_override(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_class="medium"))
        assert result.directive.tier_override == "T1_light"

    def test_cautious_reason_codes(self) -> None:
        result = _engine().evaluate(MetaMetrics(risk_class="medium"))
        assert ReasonCode.CAUTIOUS_MEDIUM_RISK_CLASS.value in result.reasons

    def test_cautious_budget_reason_code(self) -> None:
        result = _engine().evaluate(MetaMetrics(budget_remaining=0.1))
        assert ReasonCode.CAUTIOUS_BUDGET_PRESSURE.value in result.reasons


class TestNormalRule:
    def test_defaults_produce_normal(self) -> None:
        result = _engine().evaluate(MetaMetrics())
        assert result.meta_state == MetaState.NORMAL

    def test_normal_empty_directive(self) -> None:
        result = _engine().evaluate(MetaMetrics())
        assert result.directive.override_next_state is None
        assert result.directive.tier_override is None
        assert not result.directive.require_confirmation
        assert not result.directive.require_verification

    def test_normal_reason_code(self) -> None:
        result = _engine().evaluate(MetaMetrics())
        assert ReasonCode.NORMAL_DEFAULT.value in result.reasons

    def test_ruleset_version_in_result(self) -> None:
        result = _engine().evaluate(MetaMetrics())
        assert result.ruleset_version == "metactl.v1"

    def test_custom_ruleset_version(self) -> None:
        cfg = MetaConfig(ruleset_version="metactl.v2-test")
        result = _engine(cfg).evaluate(MetaMetrics())
        assert result.ruleset_version == "metactl.v2-test"
