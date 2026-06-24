from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .progress import SbspBudgetCeilings, SbspBudgetUsage
from ..missions import ToolReversibility

AutonomyThresholdAxis = Literal[
    "reversibility",
    "cost",
    "factual_prerequisite",
    "stylistic_preference",
]

FactualPrerequisiteReason = Literal[
    "missing_credential",
    "missing_typed_field",
    "missing_required_record",
]


class ReversibilityAxisConfig(BaseModel):
    """Typed operator-config for the reversibility axis."""

    model_config = ConfigDict(extra="forbid")

    triggering_levels: tuple[ToolReversibility, ...] = Field(
        default=("irreversible",),
        description=(
            "Closed-set tuple of ``ToolReversibility`` levels that fire "
            "the reversibility axis. Defaults to MTRR's single hard-stop "
            "level (``irreversible``). Empty tuple disables the axis."
        ),
    )

    @model_validator(mode="after")
    def _validate_unique_levels(self) -> "ReversibilityAxisConfig":
        if len(set(self.triggering_levels)) != len(self.triggering_levels):
            raise ValueError(
                "ReversibilityAxisConfig.triggering_levels must be unique."
            )
        return self


class CostAxisConfig(BaseModel):
    """Typed operator-config for the cost axis."""

    model_config = ConfigDict(extra="forbid")

    ceilings: SbspBudgetCeilings = Field(
        default_factory=SbspBudgetCeilings,
        description=(
            "APBR ``SbspBudgetCeilings`` record. AATR consults only the "
            "``max_wall_clock_seconds`` and ``max_dollar_cost_cents`` "
            "axes for clarification triggering; iteration / tool-call "
            "ceilings stay SBSP-precedence territory via APBR."
        ),
    )


class FactualPrerequisiteAxisConfig(BaseModel):
    """Typed operator-config for the factual-prerequisite axis."""

    model_config = ConfigDict(extra="forbid")

    enabled_reasons: tuple[FactualPrerequisiteReason, ...] = Field(
        default=(
            "missing_credential",
            "missing_typed_field",
            "missing_required_record",
        ),
        description=(
            "Closed-set tuple of ``FactualPrerequisiteReason`` values "
            "that fire the factual-prerequisite axis. Defaults to all "
            "three structural causes. Empty tuple disables the axis."
        ),
    )

    @model_validator(mode="after")
    def _validate_unique_reasons(self) -> "FactualPrerequisiteAxisConfig":
        if len(set(self.enabled_reasons)) != len(self.enabled_reasons):
            raise ValueError(
                "FactualPrerequisiteAxisConfig.enabled_reasons must be unique."
            )
        return self


class StylisticPreferenceAxisConfig(BaseModel):
    """Typed operator-config for the stylistic-preference axis."""

    model_config = ConfigDict(extra="forbid")

    documented_dimensions: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Informational tuple of stylistic dimensions the operator "
            "has documented as never-clarifiable. Pure documentation; "
            "the composer hard-codes ``stylistic_triggered=False`` "
            "regardless of this field."
        ),
    )


class AutonomyThresholdConfig(BaseModel):
    """Operator-config-owned four-axis clarification-trigger contract."""

    model_config = ConfigDict(extra="forbid")

    reversibility: ReversibilityAxisConfig = Field(
        default_factory=ReversibilityAxisConfig
    )
    cost: CostAxisConfig = Field(default_factory=CostAxisConfig)
    factual_prerequisite: FactualPrerequisiteAxisConfig = Field(
        default_factory=FactualPrerequisiteAxisConfig
    )
    stylistic_preference: StylisticPreferenceAxisConfig = Field(
        default_factory=StylisticPreferenceAxisConfig
    )


class ReversibilityAxisInput(BaseModel):
    """Typed structural input for the reversibility axis."""

    model_config = ConfigDict(extra="forbid")

    action_reversibility: ToolReversibility | None = Field(
        default=None,
        description=(
            "MTRR ``ToolReversibility`` classification of the active "
            "action. ``None`` when no action / capability classification "
            "is in play this turn."
        ),
    )


class CostAxisInput(BaseModel):
    """Typed structural input for the cost axis."""

    model_config = ConfigDict(extra="forbid")

    usage: SbspBudgetUsage = Field(default_factory=SbspBudgetUsage)


class FactualPrerequisiteAxisInput(BaseModel):
    """Typed structural input for the factual-prerequisite axis."""

    model_config = ConfigDict(extra="forbid")

    missing_reasons: tuple[FactualPrerequisiteReason, ...] = Field(
        default_factory=tuple
    )

    @model_validator(mode="after")
    def _validate_unique_missing(self) -> "FactualPrerequisiteAxisInput":
        if len(set(self.missing_reasons)) != len(self.missing_reasons):
            raise ValueError(
                "FactualPrerequisiteAxisInput.missing_reasons must be unique."
            )
        return self


class ClarificationTrigger(BaseModel):
    """Typed structural clarification-trigger record."""

    model_config = ConfigDict(extra="forbid")

    triggered: bool
    triggering_axes: tuple[AutonomyThresholdAxis, ...] = Field(default_factory=tuple)
    triggering_reversibility_level: ToolReversibility | None = None
    triggering_cost_axes: tuple[
        Literal["wall_clock_seconds", "dollar_cost_cents"], ...
    ] = Field(default_factory=tuple)
    triggering_factual_reasons: tuple[FactualPrerequisiteReason, ...] = Field(
        default_factory=tuple
    )
    stylistic_triggered: Literal[False] = False


def _evaluate_reversibility_axis(
    *,
    config: ReversibilityAxisConfig,
    input_: ReversibilityAxisInput,
) -> tuple[bool, ToolReversibility | None]:
    """Structural evaluator for the reversibility axis."""

    if not config.triggering_levels:
        return False, None
    if input_.action_reversibility is None:
        return False, None
    if input_.action_reversibility in config.triggering_levels:
        return True, input_.action_reversibility
    return False, None


def _evaluate_cost_axis(
    *,
    config: CostAxisConfig,
    input_: CostAxisInput,
) -> tuple[bool, tuple[Literal["wall_clock_seconds", "dollar_cost_cents"], ...]]:
    """Structural evaluator for the cost axis."""

    exceeded: list[Literal["wall_clock_seconds", "dollar_cost_cents"]] = []
    ceilings = config.ceilings
    usage = input_.usage
    if (
        ceilings.max_wall_clock_seconds is not None
        and usage.wall_clock_seconds_used >= ceilings.max_wall_clock_seconds
    ):
        exceeded.append("wall_clock_seconds")
    if (
        ceilings.max_dollar_cost_cents is not None
        and usage.dollar_cost_cents_used >= ceilings.max_dollar_cost_cents
    ):
        exceeded.append("dollar_cost_cents")
    return bool(exceeded), tuple(exceeded)


def _evaluate_factual_prerequisite_axis(
    *,
    config: FactualPrerequisiteAxisConfig,
    input_: FactualPrerequisiteAxisInput,
) -> tuple[bool, tuple[FactualPrerequisiteReason, ...]]:
    """Structural evaluator for the factual-prerequisite axis."""

    if not config.enabled_reasons:
        return False, tuple()
    enabled = set(config.enabled_reasons)
    fired: list[FactualPrerequisiteReason] = [
        reason for reason in input_.missing_reasons if reason in enabled
    ]
    return bool(fired), tuple(fired)


def evaluate_autonomy_threshold(
    *,
    config: AutonomyThresholdConfig,
    reversibility_input: ReversibilityAxisInput | None = None,
    cost_input: CostAxisInput | None = None,
    factual_input: FactualPrerequisiteAxisInput | None = None,
) -> ClarificationTrigger:
    """Pure composer for ``ClarificationTrigger``."""

    rev_input = reversibility_input or ReversibilityAxisInput()
    cst_input = cost_input or CostAxisInput()
    fct_input = factual_input or FactualPrerequisiteAxisInput()

    rev_fired, rev_level = _evaluate_reversibility_axis(
        config=config.reversibility, input_=rev_input
    )
    cst_fired, cst_axes = _evaluate_cost_axis(config=config.cost, input_=cst_input)
    fct_fired, fct_reasons = _evaluate_factual_prerequisite_axis(
        config=config.factual_prerequisite, input_=fct_input
    )

    axes: list[AutonomyThresholdAxis] = []
    if rev_fired:
        axes.append("reversibility")
    if cst_fired:
        axes.append("cost")
    if fct_fired:
        axes.append("factual_prerequisite")

    return ClarificationTrigger(
        triggered=bool(axes),
        triggering_axes=tuple(axes),
        triggering_reversibility_level=rev_level,
        triggering_cost_axes=cst_axes,
        triggering_factual_reasons=fct_reasons,
        stylistic_triggered=False,
    )


__all__ = [
    "AutonomyThresholdAxis",
    "AutonomyThresholdConfig",
    "ClarificationTrigger",
    "CostAxisConfig",
    "CostAxisInput",
    "FactualPrerequisiteAxisConfig",
    "FactualPrerequisiteAxisInput",
    "FactualPrerequisiteReason",
    "ReversibilityAxisConfig",
    "ReversibilityAxisInput",
    "StylisticPreferenceAxisConfig",
    "evaluate_autonomy_threshold",
]
