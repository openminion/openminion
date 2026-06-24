from __future__ import annotations

import importlib

from openminion.modules.brain.schemas.autonomy.progress import (
    SbspBudgetCeilings,
    SbspBudgetUsage,
)
from openminion.modules.brain.schemas.autonomy.threshold import (
    AutonomyThresholdConfig,
    ClarificationTrigger,
    CostAxisConfig,
    CostAxisInput,
    FactualPrerequisiteAxisInput,
    ReversibilityAxisInput,
    StylisticPreferenceAxisConfig,
    evaluate_autonomy_threshold,
)


def _operator_threshold_config() -> AutonomyThresholdConfig:
    return AutonomyThresholdConfig(
        cost=CostAxisConfig(
            ceilings=SbspBudgetCeilings(
                max_wall_clock_seconds=300,
                max_dollar_cost_cents=500,
            )
        ),
        stylistic_preference=StylisticPreferenceAxisConfig(
            documented_dimensions=("color_choice", "tone")
        ),
    )


def test_integration_reversibility_axis_triggers() -> None:
    config = _operator_threshold_config()
    trigger = evaluate_autonomy_threshold(
        config=config,
        reversibility_input=ReversibilityAxisInput(action_reversibility="irreversible"),
    )
    assert isinstance(trigger, ClarificationTrigger)
    assert trigger.triggered is True
    assert "reversibility" in trigger.triggering_axes
    assert trigger.triggering_reversibility_level == "irreversible"
    # Other axes did not fire on this turn.
    assert "cost" not in trigger.triggering_axes
    assert "factual_prerequisite" not in trigger.triggering_axes
    assert trigger.stylistic_triggered is False


def test_integration_cost_axis_triggers_on_dollar_cost() -> None:
    config = _operator_threshold_config()
    trigger = evaluate_autonomy_threshold(
        config=config,
        cost_input=CostAxisInput(usage=SbspBudgetUsage(dollar_cost_cents_used=500)),
    )
    assert trigger.triggered is True
    assert trigger.triggering_axes == ("cost",)
    assert trigger.triggering_cost_axes == ("dollar_cost_cents",)


def test_integration_factual_axis_triggers_on_missing_credential() -> None:
    config = _operator_threshold_config()
    trigger = evaluate_autonomy_threshold(
        config=config,
        factual_input=FactualPrerequisiteAxisInput(
            missing_reasons=("missing_credential",)
        ),
    )
    assert trigger.triggered is True
    assert trigger.triggering_axes == ("factual_prerequisite",)
    assert trigger.triggering_factual_reasons == ("missing_credential",)


def test_integration_stylistic_axis_does_not_trigger() -> None:
    config = _operator_threshold_config()
    trigger = evaluate_autonomy_threshold(config=config)
    assert trigger.triggered is False
    assert trigger.stylistic_triggered is False
    assert "stylistic_preference" not in trigger.triggering_axes


def test_integration_operator_threshold_change_flows_through() -> None:
    tight = AutonomyThresholdConfig(
        cost=CostAxisConfig(ceilings=SbspBudgetCeilings(max_dollar_cost_cents=100))
    )
    usage = CostAxisInput(usage=SbspBudgetUsage(dollar_cost_cents_used=100))
    assert evaluate_autonomy_threshold(config=tight, cost_input=usage).triggered is True

    loose = AutonomyThresholdConfig(
        cost=CostAxisConfig(ceilings=SbspBudgetCeilings(max_dollar_cost_cents=1_000))
    )
    assert (
        evaluate_autonomy_threshold(config=loose, cost_input=usage).triggered is False
    )


def test_integration_clarify_owner_module_remains_importable() -> None:
    module = importlib.import_module("openminion.modules.brain.loop.clarify")
    assert module is not None


def test_integration_composer_emits_typed_event_without_calling_clarify() -> None:
    config = _operator_threshold_config()
    trigger = evaluate_autonomy_threshold(
        config=config,
        reversibility_input=ReversibilityAxisInput(action_reversibility="irreversible"),
    )
    assert isinstance(trigger, ClarificationTrigger)
    assert trigger.triggered is True
    assert "reversibility" in trigger.triggering_axes


def test_integration_all_axes_compose_or_with_stylistic_excluded() -> None:
    config = _operator_threshold_config()
    trigger = evaluate_autonomy_threshold(
        config=config,
        reversibility_input=ReversibilityAxisInput(action_reversibility="irreversible"),
        cost_input=CostAxisInput(
            usage=SbspBudgetUsage(
                wall_clock_seconds_used=350,
                dollar_cost_cents_used=600,
            )
        ),
        factual_input=FactualPrerequisiteAxisInput(
            missing_reasons=("missing_typed_field",)
        ),
    )
    assert trigger.triggered is True
    assert trigger.triggering_axes == (
        "reversibility",
        "cost",
        "factual_prerequisite",
    )
    assert set(trigger.triggering_cost_axes) == {
        "wall_clock_seconds",
        "dollar_cost_cents",
    }
    assert trigger.triggering_factual_reasons == ("missing_typed_field",)
    assert trigger.stylistic_triggered is False
