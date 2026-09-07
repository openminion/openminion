from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from openminion.modules.task.constants import (
    TASK_PLAN_OUTPUT_SUMMARY_MAX_CHARS,
    TASK_PLAN_TOOL_FAMILIES,
)

TaskPlanStatus = Literal["active", "completed", "abandoned"]
TaskPlanStepStatus = Literal["pending", "in_progress", "completed", "blocked"]
TaskPlanDifficulty = Literal["low", "medium", "high"]
TaskPlanToolFamily = Literal[
    "browser",
    "code",
    "exec",
    "fetch",
    "file",
    "ip",
    "location",
    "search",
    "skill",
    "task",
    "time",
    "utility",
    "weather",
    "web",
]


def _trimmed_non_empty(value: Any) -> str:
    return str(value or "").strip()


def _bounded_output_summary(value: Any) -> str:
    text = _trimmed_non_empty(value)
    if len(text) <= TASK_PLAN_OUTPUT_SUMMARY_MAX_CHARS:
        return text
    return text[:TASK_PLAN_OUTPUT_SUMMARY_MAX_CHARS].rstrip()


class TaskPlanStep(BaseModel):
    """Neutral transport DTO for a session-scoped autonomous plan step."""

    step_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    status: TaskPlanStepStatus = "pending"
    estimated_difficulty: TaskPlanDifficulty = "medium"
    depends_on: list[str] = Field(default_factory=list)
    tool_families: list[TaskPlanToolFamily] = Field(default_factory=list)
    output_summary: str = ""
    blocker_type: str | None = None
    blocker_details: str | None = None

    @field_validator("step_id", "description", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return _trimmed_non_empty(value)

    @field_validator("depends_on", mode="before")
    @classmethod
    def _normalize_depends_on(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("depends_on must be a list of step ids")
        return [_trimmed_non_empty(item) for item in value if _trimmed_non_empty(item)]

    @field_validator("tool_families", mode="before")
    @classmethod
    def _normalize_tool_families(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("tool_families must be a list")
        families: list[str] = []
        for item in value:
            family = _trimmed_non_empty(item)
            if not family:
                continue
            if family not in TASK_PLAN_TOOL_FAMILIES:
                raise ValueError(f"unsupported tool_family: {family}")
            if family not in families:
                families.append(family)
        return families

    @field_validator("output_summary", mode="before")
    @classmethod
    def _cap_output_summary(cls, value: Any) -> str:
        return _bounded_output_summary(value)

    @field_validator("blocker_type", "blocker_details", mode="before")
    @classmethod
    def _optional_text(cls, value: Any) -> str | None:
        return _trimmed_non_empty(value) or None


class TaskPlan(BaseModel):
    """Session-scoped model-authored plan carried through context/session layers."""

    plan_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    workflow_id: str | None = None
    workflow_version_hash: str | None = None
    root_goal_id: str | None = None
    criterion_ids: list[str] = Field(default_factory=list)
    status: TaskPlanStatus = "active"
    steps: list[TaskPlanStep] = Field(min_length=1)
    continue_plan_autonomously: bool = False

    @field_validator("plan_id", "objective", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return _trimmed_non_empty(value)

    @field_validator(
        "workflow_id", "workflow_version_hash", "root_goal_id", mode="before"
    )
    @classmethod
    def _optional_identifier(cls, value: Any) -> str | None:
        return _trimmed_non_empty(value) or None

    @model_validator(mode="after")
    def _validate_step_graph(self) -> "TaskPlan":
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("TaskPlan steps must have unique step_id values")
        known_ids = set(step_ids)
        for step in self.steps:
            for dependency in step.depends_on:
                if dependency not in known_ids:
                    raise ValueError(
                        f"TaskPlan step {step.step_id!r} depends on unknown step {dependency!r}"
                    )

        visiting: set[str] = set()
        visited: set[str] = set()
        deps_by_step = {step.step_id: step.depends_on for step in self.steps}

        def visit(step_id: str) -> None:
            if step_id in visited:
                return
            if step_id in visiting:
                raise ValueError("TaskPlan dependencies must be acyclic")
            visiting.add(step_id)
            for dependency in deps_by_step[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in step_ids:
            visit(step_id)
        return self


class TaskPlanStepCompleted(BaseModel):
    plan_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    outcome: str = ""
    output_summary: str = ""
    continue_plan_autonomously: bool = False

    @field_validator("plan_id", "step_id", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return _trimmed_non_empty(value)

    @field_validator("output_summary", mode="before")
    @classmethod
    def _cap_output_summary(cls, value: Any) -> str:
        return _bounded_output_summary(value)

    @field_validator("outcome", mode="before")
    @classmethod
    def _strip_outcome(cls, value: Any) -> str:
        return _trimmed_non_empty(value)


class TaskPlanStepBlocked(BaseModel):
    plan_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    blocker_type: str = Field(min_length=1)
    blocker_details: str = ""

    @field_validator("plan_id", "step_id", "blocker_type", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return _trimmed_non_empty(value)

    @field_validator("blocker_details", mode="before")
    @classmethod
    def _strip_details(cls, value: Any) -> str:
        return _trimmed_non_empty(value)


class TaskPlanRevision(BaseModel):
    plan_id: str = Field(min_length=1)
    revision_id: str | None = None
    predecessor_revision_id: str | None = None
    criterion_ids: list[str] = Field(default_factory=list)
    verifier_refs: list[str] = Field(default_factory=list)
    reason: str = ""
    revised_steps: list[TaskPlanStep] = Field(min_length=1)
    objective: str | None = None
    workflow_id: str | None = None
    workflow_version_hash: str | None = None
    continue_plan_autonomously: bool = False

    @field_validator("plan_id", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return _trimmed_non_empty(value)

    @field_validator("reason", mode="before")
    @classmethod
    def _strip_reason(cls, value: Any) -> str:
        return _trimmed_non_empty(value)

    @field_validator(
        "revision_id",
        "predecessor_revision_id",
        "objective",
        "workflow_id",
        "workflow_version_hash",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: Any) -> str | None:
        return _trimmed_non_empty(value) or None

    def to_task_plan(
        self,
        *,
        fallback_objective: str,
        fallback_workflow_id: str | None = None,
        fallback_workflow_version_hash: str | None = None,
        fallback_criterion_ids: list[str] | None = None,
    ) -> TaskPlan:
        return TaskPlan(
            plan_id=self.plan_id,
            objective=self.objective or fallback_objective,
            workflow_id=self.workflow_id or fallback_workflow_id,
            workflow_version_hash=(
                self.workflow_version_hash or fallback_workflow_version_hash
            ),
            root_goal_id=None,
            criterion_ids=self.criterion_ids or list(fallback_criterion_ids or []),
            status="active",
            steps=list(self.revised_steps),
            continue_plan_autonomously=self.continue_plan_autonomously,
        )


class TaskPlanTerminalSignal(BaseModel):
    plan_id: str = Field(min_length=1)
    reason: str = ""

    @field_validator("plan_id", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return _trimmed_non_empty(value)

    @field_validator("reason", mode="before")
    @classmethod
    def _strip_reason(cls, value: Any) -> str:
        return _trimmed_non_empty(value)


def apply_task_plan_signals(
    plan: TaskPlan | None,
    *,
    step_completed: TaskPlanStepCompleted | None = None,
    step_blocked: TaskPlanStepBlocked | None = None,
    abandoned: TaskPlanTerminalSignal | None = None,
    completed: TaskPlanTerminalSignal | None = None,
) -> TaskPlan | None:
    signals = tuple(
        signal
        for signal in (step_completed, step_blocked, abandoned, completed)
        if signal is not None
    )
    if not signals:
        return plan
    if plan is None:
        raise ValueError("task plan progress requires a checkpoint task plan")
    if any(signal.plan_id != plan.plan_id for signal in signals):
        raise ValueError("task plan progress must match the checkpoint task plan")

    steps = list(plan.steps)
    for signal, step_status in (
        (step_completed, "completed"),
        (step_blocked, "blocked"),
    ):
        if signal is None:
            continue
        matching = [step for step in steps if step.step_id == signal.step_id]
        if not matching:
            raise ValueError(f"unknown task plan step: {signal.step_id}")
        steps = [
            step.model_copy(
                update={
                    "status": step_status,
                    "output_summary": (
                        signal.output_summary
                        if isinstance(signal, TaskPlanStepCompleted)
                        else step.output_summary
                    ),
                    "blocker_type": (
                        signal.blocker_type
                        if isinstance(signal, TaskPlanStepBlocked)
                        else None
                    ),
                    "blocker_details": (
                        signal.blocker_details
                        if isinstance(signal, TaskPlanStepBlocked)
                        else None
                    ),
                }
            )
            if step.step_id == signal.step_id
            else step
            for step in steps
        ]

    status: TaskPlanStatus = plan.status
    if abandoned is not None:
        status = "abandoned"
    if completed is not None:
        if any(step.status != "completed" for step in steps):
            raise ValueError("task plan completion requires every step to be completed")
        status = "completed"
    return plan.model_copy(update={"steps": steps, "status": status})


__all__ = [
    "apply_task_plan_signals",
    "TaskPlan",
    "TaskPlanDifficulty",
    "TaskPlanRevision",
    "TaskPlanStatus",
    "TaskPlanStep",
    "TaskPlanStepBlocked",
    "TaskPlanStepCompleted",
    "TaskPlanStepStatus",
    "TaskPlanTerminalSignal",
    "TaskPlanToolFamily",
]
