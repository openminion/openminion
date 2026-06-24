from __future__ import annotations

from openminion.modules.brain.meta import (
    MetaConfig,
    MetaMetrics,
    MetaRulesEngine,
    MetaState,
)


def _evaluate(metrics: MetaMetrics, **config_overrides: object):
    engine = MetaRulesEngine(MetaConfig(**config_overrides))
    return engine.evaluate(metrics)


def test_panic_on_user_kill() -> None:
    result = _evaluate(MetaMetrics(user_kill_requested=True))
    assert result.meta_state == MetaState.PANIC
    assert result.directive.override_next_state == "STOPPED"
    assert result.directive.tool_temp_denylist == ["*"]


def test_high_risk_requires_confirmation_and_verification() -> None:
    result = _evaluate(
        MetaMetrics(
            risk_class="high",
            risk_score=85,
            requires_side_effects=True,
        )
    )
    assert result.meta_state == MetaState.HIGH_ASSURANCE
    assert result.directive.tier_override == "T3_high_assurance"
    assert result.directive.require_confirmation is True
    assert result.directive.require_verification is True


def test_clarification_forces_waiting() -> None:
    result = _evaluate(
        MetaMetrics(
            needs_clarification=True,
            intent_confidence=0.4,
        )
    )
    assert result.meta_state == MetaState.CAUTIOUS
    assert result.directive.override_next_state == "WAITING"
    assert result.directive.require_clarification is True
    assert str(result.directive.clarification_question or "").strip()
    assert result.directive.escalation_question is not None


def test_recovery_on_repeated_errors() -> None:
    result = _evaluate(MetaMetrics(recent_failures=2), repeat_failure_threshold=2)
    assert result.meta_state == MetaState.RECOVERY
    assert result.directive.override_next_state == "PLAN"
    assert result.directive.prompt_constraints


def test_low_grounding_triggers_high_assurance() -> None:
    result = _evaluate(
        MetaMetrics(grounding_confidence=0.2), low_grounding_threshold=0.5
    )
    assert result.meta_state == MetaState.HIGH_ASSURANCE
    assert result.directive.require_verification is True
