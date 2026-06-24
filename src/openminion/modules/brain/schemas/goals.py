from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openminion.modules.brain.constants import MissionStatus


VerifierFamily = Literal[
    "structural",
    "freshness",
    "artifact_presence",
    "success_criteria_match",
]


FailureConditionKind = Literal[
    "deliverable_missing",
    "success_criterion_unmet",
    "budget_exhausted",
    "blocker_unresolved",
    "capability_boundary",
    "operator_cancelled",
]


ExternalBlockerKind = Literal[
    "human_approval",
    "external_signal",
    "wait_for_artifact",
]


GoalDriftSignalKind = Literal[
    "actions_diverge_from_criteria",
    "inaction_against_criteria",
    "objective_substitution",
    "mission_type_drift",
]


def _require_unique_ids(items: list[Any], attr_name: str, label: str) -> None:
    if len(items) != len({getattr(item, attr_name) for item in items}):
        raise ValueError(f"{label} must have unique {attr_name}")


class SuccessCriterion(BaseModel):
    """Structural completion condition for a typed ``Goal``."""

    model_config = ConfigDict(extra="forbid")

    criterion_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    structural_check: str = Field(
        min_length=1,
        description=(
            "Short structural-check identifier (e.g. ``artifact_present``, "
            "``success_criteria.key=value``). The runtime's verifier surface "
            "maps this to a concrete check. Free-form prose is not honored."
        ),
    )

    @field_validator("criterion_id", "description", "structural_check", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        return str(value or "").strip()


class Deliverable(BaseModel):
    """Required artifact/action contract for a typed ``Goal``."""

    model_config = ConfigDict(extra="forbid")

    deliverable_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    verification_hint: VerifierFamily = Field(
        default="artifact_presence",
        description=(
            "TGCR-Q2: advisory verifier-family hint for which verifier "
            "should confirm delivery. Runtime dispatch is independent; the "
            "hint exists so ``Deliverable`` records are closed under the "
            "same structural-verifier vocabulary."
        ),
    )

    @field_validator("deliverable_id", "description", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        return str(value or "").strip()


class FailureCondition(BaseModel):
    """Structural terminal-failure trigger for a typed ``Goal``."""

    model_config = ConfigDict(extra="forbid")

    condition_id: str = Field(min_length=1)
    kind: FailureConditionKind
    description: str = Field(min_length=1)

    @field_validator("condition_id", "description", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        return str(value or "").strip()


class ExternalBlocker(BaseModel):
    """Typed external dependency that prevents automatic goal resume."""

    model_config = ConfigDict(extra="forbid")

    blocker_id: str = Field(min_length=1)
    kind: ExternalBlockerKind
    descriptor: str = Field(min_length=1)
    created_at: str = Field(min_length=1)

    @field_validator("blocker_id", "descriptor", "created_at", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return str(value or "").strip()


class LifecycleAuditRecord(BaseModel):
    """Typed goal/mission lifecycle audit row."""

    model_config = ConfigDict(extra="forbid")

    entity_kind: Literal["goal", "mission"]
    entity_id: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    prior_status: str | None = None
    new_status: str | None = None
    reason: str = ""
    actor: str = ""
    action_authorization: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "entity_id",
        "timestamp",
        "reason",
        "actor",
        "prior_status",
        "new_status",
        mode="before",
    )
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value or "").strip()
        return text or None


class Milestone(BaseModel):
    """Typed progress checkpoint for a long-running goal."""

    model_config = ConfigDict(extra="forbid")

    milestone_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    structural_check: str = Field(min_length=1)

    @field_validator("milestone_id", "description", "structural_check", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        return str(value or "").strip()


class GoalDriftSignal(BaseModel):
    """Typed goal-drift signal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    kind: GoalDriftSignalKind
    description: str = Field(min_length=1)
    detected_at: str = Field(min_length=1)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "signal_id", "goal_id", "description", "detected_at", mode="before"
    )
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return str(value or "").strip()


class Goal(BaseModel):
    """Goal contract."""

    model_config = ConfigDict(extra="forbid")

    goal_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    success_criteria: list[SuccessCriterion] = Field(min_length=1)
    deliverables: list[Deliverable] = Field(min_length=1)
    failure_conditions: list[FailureCondition] = Field(default_factory=list)
    status: MissionStatus = Field(
        default=MissionStatus.ACTIVE,
        description=(
            "LGMH-01: additive goal lifecycle field. Reuses the existing "
            "mission-status vocabulary instead of inventing a third parallel "
            "status owner."
        ),
    )
    apd_plan_id: str | None = Field(
        default=None,
        description=(
            "Optional APD plan id (``TaskPlan.plan_id``) that decomposes "
            "this goal. Goal decomposition reuses APD typed plan/step "
            "continuity per TGRC `TGRC-Q5`."
        ),
    )
    parent_goal_id: str | None = Field(
        default=None,
        description=(
            "Optional parent goal id for hierarchical goal records. "
            "Mirrors ``GoalDeclaration.parent_goal_id``."
        ),
    )
    wall_clock_budget_seconds: int | None = Field(
        default=None,
        ge=1,
        description=("LGMH-07 additive wall-clock budget carried on the goal itself."),
    )
    milestone_checkpoints: list[Milestone] = Field(default_factory=list)
    cost_budget_tokens: int | None = Field(default=None, ge=1)
    cost_budget_dollars: float | None = Field(default=None, gt=0)
    external_blockers: list[ExternalBlocker] = Field(default_factory=list)
    owner_agent_id: str | None = None

    @field_validator("goal_id", "description", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("apd_plan_id", "parent_goal_id", "owner_agent_id", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @model_validator(mode="after")
    def _validate_non_conflation(self) -> "Goal":
        for items, attr_name, label in (
            (self.success_criteria, "criterion_id", "Goal.success_criteria"),
            (self.deliverables, "deliverable_id", "Goal.deliverables"),
            (self.failure_conditions, "condition_id", "Goal.failure_conditions"),
            (
                self.milestone_checkpoints,
                "milestone_id",
                "Goal.milestone_checkpoints",
            ),
            (self.external_blockers, "blocker_id", "Goal.external_blockers"),
        ):
            _require_unique_ids(items, attr_name, label)
        return self


GoalStatus = MissionStatus

_ALLOWED_GOAL_STATUS_TRANSITIONS: dict[GoalStatus, set[GoalStatus]] = {
    GoalStatus.ACTIVE: {
        GoalStatus.ACTIVE,
        GoalStatus.PAUSED,
        GoalStatus.AWAITING_ASYNC,
        GoalStatus.COMPLETED,
        GoalStatus.CANCELLED,
        GoalStatus.HALTED,
    },
    GoalStatus.PAUSED: {
        GoalStatus.PAUSED,
        GoalStatus.ACTIVE,
        GoalStatus.CANCELLED,
        GoalStatus.HALTED,
    },
    GoalStatus.AWAITING_ASYNC: {
        GoalStatus.AWAITING_ASYNC,
        GoalStatus.ACTIVE,
        GoalStatus.PAUSED,
        GoalStatus.COMPLETED,
        GoalStatus.CANCELLED,
        GoalStatus.HALTED,
    },
    GoalStatus.COMPLETED: {GoalStatus.COMPLETED},
    GoalStatus.CANCELLED: {GoalStatus.CANCELLED},
    GoalStatus.HALTED: {GoalStatus.HALTED},
}


def validate_goal_status_transition(
    prior_status: GoalStatus | str,
    new_status: GoalStatus | str,
) -> GoalStatus:
    """Validate and normalize a goal-status transition."""

    prior = GoalStatus(str(prior_status))
    new = GoalStatus(str(new_status))
    if new not in _ALLOWED_GOAL_STATUS_TRANSITIONS[prior]:
        raise ValueError(
            f"Illegal Goal.status transition: {prior.value!r} -> {new.value!r}"
        )
    return new


def build_operator_cancelled_failure_condition(
    *, goal_id: str, reason: str = ""
) -> FailureCondition:
    """Build the canonical structural failure condition for operator abort."""

    normalized_goal_id = str(goal_id or "").strip() or "goal"
    normalized_reason = str(reason or "").strip() or "operator_cancelled"
    return FailureCondition(
        condition_id=f"{normalized_goal_id}-operator-cancelled",
        kind="operator_cancelled",
        description=normalized_reason,
    )


def evaluate_goal_wall_clock_budget(
    goal: Goal,
    *,
    elapsed_seconds: int | float,
) -> FailureCondition | None:
    """Return a structural failure condition when the wall-clock budget is exhausted."""

    budget = goal.wall_clock_budget_seconds
    if budget is None:
        return None
    if float(elapsed_seconds) <= float(budget):
        return None
    return FailureCondition(
        condition_id=f"{goal.goal_id}-wall-clock-budget",
        kind="budget_exhausted",
        description="wall_clock_budget",
    )


def evaluate_goal_cost_budget(
    goal: Goal,
    *,
    consumed_tokens: int = 0,
    consumed_dollars: float = 0.0,
) -> FailureCondition | None:
    """Return a structural failure condition when cumulative cost is exhausted."""

    token_budget = goal.cost_budget_tokens
    if token_budget is not None and int(consumed_tokens) > int(token_budget):
        return FailureCondition(
            condition_id=f"{goal.goal_id}-cost-budget-tokens",
            kind="budget_exhausted",
            description="cost_budget",
        )
    dollar_budget = goal.cost_budget_dollars
    if dollar_budget is not None and float(consumed_dollars) > float(dollar_budget):
        return FailureCondition(
            condition_id=f"{goal.goal_id}-cost-budget-dollars",
            kind="budget_exhausted",
            description="cost_budget",
        )
    return None


def goal_has_unresolved_external_blockers(goal: Goal) -> bool:
    """Return True when a goal still has unresolved external blockers."""

    return bool(goal.external_blockers)


__all__ = [
    "Deliverable",
    "ExternalBlocker",
    "ExternalBlockerKind",
    "FailureCondition",
    "FailureConditionKind",
    "Goal",
    "GoalDriftSignal",
    "GoalDriftSignalKind",
    "LifecycleAuditRecord",
    "GoalStatus",
    "Milestone",
    "SuccessCriterion",
    "VerifierFamily",
    "build_operator_cancelled_failure_condition",
    "evaluate_goal_cost_budget",
    "evaluate_goal_wall_clock_budget",
    "goal_has_unresolved_external_blockers",
    "validate_goal_status_transition",
]
