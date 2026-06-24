from __future__ import annotations

import pytest

from openminion.modules.brain.meta.evaluator import MetaRulesEngine
from openminion.modules.brain.meta.schemas import (
    MetaDirective,
    MetaMetrics,
    VerificationMode,
)


def _directive(metrics: MetaMetrics) -> MetaDirective:
    return MetaRulesEngine().evaluate(metrics).directive


def test_panic_directive_fields() -> None:
    directive = _directive(MetaMetrics(user_kill_requested=True))
    assert directive.override_next_state == "STOPPED"
    assert "*" in directive.tool_temp_denylist
    assert directive.note_to_user is not None


def test_recovery_directive_fields() -> None:
    directive = _directive(MetaMetrics(recent_failures=5))
    assert directive.override_next_state == "PLAN"
    assert directive.prompt_constraints
    assert directive.tier_override is None


def test_high_assurance_directive_fields() -> None:
    directive = _directive(MetaMetrics(risk_class="high"))
    assert directive.tier_override == "T3_high_assurance"
    assert directive.require_verification
    assert directive.verification_mode == VerificationMode.panel_judge
    assert directive.prompt_constraints
    assert not directive.require_confirmation


def test_high_assurance_confirmation_with_side_effects() -> None:
    directive = _directive(MetaMetrics(risk_class="high", requires_side_effects=True))
    assert directive.require_confirmation


def test_high_assurance_ttl_fields() -> None:
    assert _directive(MetaMetrics(risk_class="high")).ttl_ticks is None


def test_cautious_directive_fields() -> None:
    directive = _directive(MetaMetrics(risk_class="medium"))
    assert directive.tier_override == "T1_light"
    assert not directive.require_verification


def test_clarification_sets_waiting_state_and_question() -> None:
    directive = _directive(MetaMetrics(needs_clarification=True))
    assert directive.override_next_state == "WAITING"
    assert directive.escalation_question


def test_medium_risk_with_side_effects_sets_verification() -> None:
    directive = _directive(MetaMetrics(risk_class="medium", requires_side_effects=True))
    assert directive.require_verification
    assert directive.verification_mode == VerificationMode.rule_based


def test_budget_pressure_sets_budget_adjustments() -> None:
    directive = _directive(MetaMetrics(budget_remaining=0.1))
    assert directive.budget_adjustments is not None
    assert directive.budget_adjustments.lower_context_limits


def test_tool_degraded_adds_constraint() -> None:
    constraints = _directive(MetaMetrics(tool_success_rate_ewma=0.5)).prompt_constraints
    assert any(
        "tool" in constraint.lower() or "non-destructive" in constraint.lower()
        for constraint in constraints
    )


def test_normal_directive_fields() -> None:
    directive = _directive(MetaMetrics())
    assert directive.override_next_state is None
    assert directive.tier_override is None
    assert not directive.require_confirmation
    assert not directive.require_verification
    assert directive.tool_temp_denylist == []
    assert directive.tool_temp_allowlist == []
    assert directive.prompt_constraints == []
    assert directive.budget_adjustments is None


@pytest.mark.parametrize(
    "metrics",
    [
        MetaMetrics(),
        MetaMetrics(user_kill_requested=True),
        MetaMetrics(recent_failures=5),
        MetaMetrics(risk_class="high"),
        MetaMetrics(risk_class="medium"),
        MetaMetrics(needs_clarification=True),
        MetaMetrics(grounding_confidence=0.2),
        MetaMetrics(budget_remaining=0.05),
    ],
)
def test_scenarios_produce_schema_valid_directive(metrics: MetaMetrics) -> None:
    MetaDirective.model_validate(_directive(metrics).model_dump())
