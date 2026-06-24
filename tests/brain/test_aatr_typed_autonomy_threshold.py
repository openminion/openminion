from __future__ import annotations

import pytest

from openminion.modules.brain.schemas.autonomy.progress import (
    SbspBudgetCeilings,
    SbspBudgetUsage,
)
from openminion.modules.brain.schemas.autonomy.threshold import (
    AutonomyThresholdConfig,
    ClarificationTrigger,
    CostAxisConfig,
    CostAxisInput,
    FactualPrerequisiteAxisConfig,
    FactualPrerequisiteAxisInput,
    ReversibilityAxisConfig,
    ReversibilityAxisInput,
    StylisticPreferenceAxisConfig,
    evaluate_autonomy_threshold,
)
from openminion.modules.brain.schemas.missions import ToolReversibility
from openminion.modules.tool.plugin_contract import RiskReversibility


def test_autonomy_threshold_config_default_is_typed_and_conservative() -> None:
    config = AutonomyThresholdConfig()
    assert config.reversibility.triggering_levels == ("irreversible",)
    assert isinstance(config.cost.ceilings, SbspBudgetCeilings)
    assert config.cost.ceilings.max_wall_clock_seconds is None
    assert config.cost.ceilings.max_dollar_cost_cents is None
    assert config.factual_prerequisite.enabled_reasons == (
        "missing_credential",
        "missing_typed_field",
        "missing_required_record",
    )
    assert isinstance(config.stylistic_preference, StylisticPreferenceAxisConfig)
    assert config.stylistic_preference.documented_dimensions == tuple()


def test_autonomy_threshold_config_rejects_extra_fields() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AutonomyThresholdConfig(model_decided_override=True)  # type: ignore[call-arg]


def test_reversibility_alias_is_mtrr_alias_of_existing_risk_enum() -> None:
    assert ToolReversibility is RiskReversibility


def test_reversibility_axis_fires_on_irreversible() -> None:
    config = AutonomyThresholdConfig()
    trigger = evaluate_autonomy_threshold(
        config=config,
        reversibility_input=ReversibilityAxisInput(action_reversibility="irreversible"),
    )
    assert isinstance(trigger, ClarificationTrigger)
    assert trigger.triggered is True
    assert trigger.triggering_axes == ("reversibility",)
    assert trigger.triggering_reversibility_level == "irreversible"


def test_reversibility_axis_fires_on_operator_added_partially_reversible() -> None:
    config = AutonomyThresholdConfig(
        reversibility=ReversibilityAxisConfig(
            triggering_levels=("irreversible", "partially_reversible")
        )
    )
    trigger = evaluate_autonomy_threshold(
        config=config,
        reversibility_input=ReversibilityAxisInput(
            action_reversibility="partially_reversible"
        ),
    )
    assert trigger.triggered is True
    assert trigger.triggering_axes == ("reversibility",)
    assert trigger.triggering_reversibility_level == "partially_reversible"


def test_reversibility_axis_does_not_fire_on_reversible() -> None:
    config = AutonomyThresholdConfig()
    trigger = evaluate_autonomy_threshold(
        config=config,
        reversibility_input=ReversibilityAxisInput(action_reversibility="reversible"),
    )
    assert trigger.triggered is False
    assert trigger.triggering_axes == tuple()
    assert trigger.triggering_reversibility_level is None


def test_reversibility_axis_disabled_when_levels_tuple_empty() -> None:
    config = AutonomyThresholdConfig(
        reversibility=ReversibilityAxisConfig(triggering_levels=())
    )
    trigger = evaluate_autonomy_threshold(
        config=config,
        reversibility_input=ReversibilityAxisInput(action_reversibility="irreversible"),
    )
    assert trigger.triggered is False


def test_reversibility_axis_unique_levels_required() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReversibilityAxisConfig(triggering_levels=("irreversible", "irreversible"))


def test_cost_axis_fires_on_wall_clock_ceiling() -> None:
    config = AutonomyThresholdConfig(
        cost=CostAxisConfig(ceilings=SbspBudgetCeilings(max_wall_clock_seconds=10))
    )
    trigger = evaluate_autonomy_threshold(
        config=config,
        cost_input=CostAxisInput(usage=SbspBudgetUsage(wall_clock_seconds_used=10)),
    )
    assert trigger.triggered is True
    assert trigger.triggering_axes == ("cost",)
    assert trigger.triggering_cost_axes == ("wall_clock_seconds",)


def test_cost_axis_fires_on_dollar_cost_ceiling() -> None:
    config = AutonomyThresholdConfig(
        cost=CostAxisConfig(ceilings=SbspBudgetCeilings(max_dollar_cost_cents=500))
    )
    trigger = evaluate_autonomy_threshold(
        config=config,
        cost_input=CostAxisInput(usage=SbspBudgetUsage(dollar_cost_cents_used=500)),
    )
    assert trigger.triggered is True
    assert trigger.triggering_axes == ("cost",)
    assert trigger.triggering_cost_axes == ("dollar_cost_cents",)


def test_cost_axis_fires_on_both_axes_when_both_exceed() -> None:
    config = AutonomyThresholdConfig(
        cost=CostAxisConfig(
            ceilings=SbspBudgetCeilings(
                max_wall_clock_seconds=10, max_dollar_cost_cents=500
            )
        )
    )
    trigger = evaluate_autonomy_threshold(
        config=config,
        cost_input=CostAxisInput(
            usage=SbspBudgetUsage(
                wall_clock_seconds_used=12,
                dollar_cost_cents_used=600,
            )
        ),
    )
    assert trigger.triggered is True
    assert trigger.triggering_cost_axes == (
        "wall_clock_seconds",
        "dollar_cost_cents",
    )


def test_cost_axis_does_not_fire_below_ceiling() -> None:
    config = AutonomyThresholdConfig(
        cost=CostAxisConfig(ceilings=SbspBudgetCeilings(max_wall_clock_seconds=10))
    )
    trigger = evaluate_autonomy_threshold(
        config=config,
        cost_input=CostAxisInput(usage=SbspBudgetUsage(wall_clock_seconds_used=9)),
    )
    assert trigger.triggered is False
    assert trigger.triggering_cost_axes == tuple()


def test_cost_axis_does_not_fire_when_no_ceilings_set() -> None:
    config = AutonomyThresholdConfig()
    trigger = evaluate_autonomy_threshold(
        config=config,
        cost_input=CostAxisInput(
            usage=SbspBudgetUsage(
                wall_clock_seconds_used=10_000,
                dollar_cost_cents_used=1_000_000,
            )
        ),
    )
    assert trigger.triggered is False


def test_cost_axis_ignores_iteration_and_tool_call_ceilings() -> None:
    config = AutonomyThresholdConfig(
        cost=CostAxisConfig(
            ceilings=SbspBudgetCeilings(max_iterations=1, max_tool_calls=1)
        )
    )
    trigger = evaluate_autonomy_threshold(
        config=config,
        cost_input=CostAxisInput(
            usage=SbspBudgetUsage(iterations_used=1_000, tool_calls_used=1_000)
        ),
    )
    assert trigger.triggered is False


def test_factual_prerequisite_axis_fires_on_missing_credential() -> None:
    config = AutonomyThresholdConfig()
    trigger = evaluate_autonomy_threshold(
        config=config,
        factual_input=FactualPrerequisiteAxisInput(
            missing_reasons=("missing_credential",)
        ),
    )
    assert trigger.triggered is True
    assert trigger.triggering_axes == ("factual_prerequisite",)
    assert trigger.triggering_factual_reasons == ("missing_credential",)


def test_factual_prerequisite_axis_fires_on_missing_typed_field() -> None:
    config = AutonomyThresholdConfig()
    trigger = evaluate_autonomy_threshold(
        config=config,
        factual_input=FactualPrerequisiteAxisInput(
            missing_reasons=("missing_typed_field",)
        ),
    )
    assert trigger.triggered is True
    assert trigger.triggering_factual_reasons == ("missing_typed_field",)


def test_factual_prerequisite_axis_fires_on_missing_required_record() -> None:
    config = AutonomyThresholdConfig()
    trigger = evaluate_autonomy_threshold(
        config=config,
        factual_input=FactualPrerequisiteAxisInput(
            missing_reasons=("missing_required_record",)
        ),
    )
    assert trigger.triggered is True
    assert trigger.triggering_factual_reasons == ("missing_required_record",)


def test_factual_prerequisite_axis_reports_multiple_reasons() -> None:
    config = AutonomyThresholdConfig()
    trigger = evaluate_autonomy_threshold(
        config=config,
        factual_input=FactualPrerequisiteAxisInput(
            missing_reasons=(
                "missing_credential",
                "missing_typed_field",
            )
        ),
    )
    assert trigger.triggered is True
    assert trigger.triggering_factual_reasons == (
        "missing_credential",
        "missing_typed_field",
    )


def test_factual_prerequisite_axis_disabled_when_no_reasons_enabled() -> None:
    config = AutonomyThresholdConfig(
        factual_prerequisite=FactualPrerequisiteAxisConfig(enabled_reasons=())
    )
    trigger = evaluate_autonomy_threshold(
        config=config,
        factual_input=FactualPrerequisiteAxisInput(
            missing_reasons=("missing_credential",)
        ),
    )
    assert trigger.triggered is False


def test_factual_prerequisite_axis_filters_to_enabled_reasons() -> None:
    config = AutonomyThresholdConfig(
        factual_prerequisite=FactualPrerequisiteAxisConfig(
            enabled_reasons=("missing_credential",)
        )
    )
    trigger = evaluate_autonomy_threshold(
        config=config,
        factual_input=FactualPrerequisiteAxisInput(
            missing_reasons=("missing_credential", "missing_typed_field")
        ),
    )
    assert trigger.triggered is True
    # Only the enabled reason should appear.
    assert trigger.triggering_factual_reasons == ("missing_credential",)


def test_factual_prerequisite_unique_reasons_required() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FactualPrerequisiteAxisConfig(
            enabled_reasons=("missing_credential", "missing_credential")
        )
    with pytest.raises(ValidationError):
        FactualPrerequisiteAxisInput(
            missing_reasons=("missing_credential", "missing_credential")
        )


# AATR-03 + AATR-Q5: stylistic-preference axis is structurally non-triggering


def test_stylistic_axis_never_triggers_even_with_documented_dimensions() -> None:
    # Operator config records stylistic dimensions for documentation,
    # but the composer hard-codes the axis to never fire.
    config = AutonomyThresholdConfig(
        stylistic_preference=StylisticPreferenceAxisConfig(
            documented_dimensions=("color_choice", "tone", "phrasing")
        )
    )
    trigger = evaluate_autonomy_threshold(config=config)
    assert trigger.triggered is False
    assert trigger.stylistic_triggered is False
    assert "stylistic_preference" not in trigger.triggering_axes


def test_clarification_trigger_stylistic_field_cannot_be_true() -> None:
    from pydantic import ValidationError

    # Typed Literal[False] — Pydantic must reject True at construction.
    with pytest.raises(ValidationError):
        ClarificationTrigger(
            triggered=False,
            stylistic_triggered=True,  # type: ignore[arg-type]
        )


# AATR-Q4: OR composition across hard-stop axes; stylistic excluded


def test_or_composition_across_multiple_hard_stop_axes() -> None:
    config = AutonomyThresholdConfig(
        cost=CostAxisConfig(ceilings=SbspBudgetCeilings(max_dollar_cost_cents=100))
    )
    trigger = evaluate_autonomy_threshold(
        config=config,
        reversibility_input=ReversibilityAxisInput(action_reversibility="irreversible"),
        cost_input=CostAxisInput(usage=SbspBudgetUsage(dollar_cost_cents_used=100)),
        factual_input=FactualPrerequisiteAxisInput(
            missing_reasons=("missing_credential",)
        ),
    )
    assert trigger.triggered is True
    # Stable ordering: reversibility / cost / factual_prerequisite.
    assert trigger.triggering_axes == (
        "reversibility",
        "cost",
        "factual_prerequisite",
    )
    assert trigger.triggering_reversibility_level == "irreversible"
    assert trigger.triggering_cost_axes == ("dollar_cost_cents",)
    assert trigger.triggering_factual_reasons == ("missing_credential",)
    assert trigger.stylistic_triggered is False


def test_no_trigger_when_all_axes_quiet() -> None:
    config = AutonomyThresholdConfig()
    trigger = evaluate_autonomy_threshold(config=config)
    assert trigger.triggered is False
    assert trigger.triggering_axes == tuple()
    assert trigger.triggering_reversibility_level is None
    assert trigger.triggering_cost_axes == tuple()
    assert trigger.triggering_factual_reasons == tuple()
    assert trigger.stylistic_triggered is False


# AATR-Q4: composer returns an event record; does NOT call clarify owner


def test_composer_returns_typed_event_record_only() -> None:
    config = AutonomyThresholdConfig()
    trigger = evaluate_autonomy_threshold(
        config=config,
        reversibility_input=ReversibilityAxisInput(action_reversibility="irreversible"),
    )
    assert isinstance(trigger, ClarificationTrigger)
    # No callable / side-effect-bearing attribute on the typed record.
    for name in ("invoke_clarify", "send", "emit"):
        assert not hasattr(trigger, name)
