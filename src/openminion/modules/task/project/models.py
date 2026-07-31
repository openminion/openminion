from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openminion.modules.task.autonomy import (
    AutonomyRunPhase,
    AutonomyRunStatus,
    now_ms,
)
from openminion.modules.task.runtime.lifecycle import TaskLifecycleState


class _StrictProjectModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectVerificationState(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    WAIVED = "waived"
    FAILED = "failed"
    BLOCKED = "blocked"


class ProjectCycleDecision(StrEnum):
    CONTINUE = "continue"
    STOP = "stop"
    BLOCKED = "blocked"
    NEEDS_INPUT = "needs_input"


class ProjectControlAction(StrEnum):
    STATUS = "status"
    SHOW = "show"
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    REPRIORITIZE = "reprioritize"
    ANSWER_INPUT = "answer-input-request"
    EXTEND_BUDGET = "extend-budget"
    REPORT = "report"


class ProjectPermissionDecision(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"
    EXPIRED = "expired"
    BUDGET_EXCEEDED = "budget_exceeded"


class ProjectObjectiveContract(_StrictProjectModel):
    objective: str = Field(min_length=1)
    success_criteria: tuple[str, ...] = Field(min_length=1)
    verification: tuple[str, ...] = Field(min_length=1)
    milestones: tuple[str, ...] = ()
    plan: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    capabilities_required: tuple[str, ...] = ()
    operator_decisions: tuple[str, ...] = ()
    deferred_items: tuple[str, ...] = ()
    stop_continue_decision: str | None = None


class ProjectObjectiveLedger(_StrictProjectModel):
    ledger_ref: str = Field(min_length=1)
    contract: ProjectObjectiveContract
    revision_refs: tuple[str, ...] = ()
    current_revision_ref: str | None = None


class ProjectRun(_StrictProjectModel):
    project_run_id: str = Field(min_length=1)
    autonomy_run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    objective_ledger_ref: str = Field(min_length=1)
    evidence_ledger_ref: str = Field(min_length=1)
    resume_packet_ref: str = Field(min_length=1)
    operator_decision_log_ref: str = Field(min_length=1)
    capability_plan_ref: str = Field(min_length=1)
    metrics_summary_ref: str = Field(min_length=1)
    workspace_ref: str = Field(min_length=1)
    status: AutonomyRunStatus
    phase: AutonomyRunPhase
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)
    last_checkpoint_id: str | None = None
    next_wakeup_at_ms: int | None = Field(default=None, ge=0)
    blocked_reason: str | None = None
    verification_state: ProjectVerificationState = ProjectVerificationState.NOT_STARTED
    task_state: TaskLifecycleState | None = None


class ProjectCheckpoint(_StrictProjectModel):
    kind: str = "project_run"
    checkpoint_id: str = Field(min_length=1)
    project_run: ProjectRun
    payload: dict[str, object] = Field(default_factory=dict)


class ProjectCycleRecord(_StrictProjectModel):
    cycle_id: str = Field(min_length=1)
    project_run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    milestone: str = Field(min_length=1)
    intended_action: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    validation_refs: tuple[str, ...] = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)
    decision: ProjectCycleDecision
    decision_reason: str | None = None
    created_at_ms: int = Field(ge=0)


class ProjectControlResult(_StrictProjectModel):
    action: ProjectControlAction
    task_id: str = Field(min_length=1)
    state: TaskLifecycleState
    project_run_id: str | None = None
    autonomy_run_id: str | None = None
    goal_id: str | None = None
    last_checkpoint_id: str | None = None
    resume_count: int = Field(default=0, ge=0)
    priority: str | None = None
    operator_answer_count: int = Field(default=0, ge=0)
    budget_extensions: dict[str, int] = Field(default_factory=dict)
    cycle_count: int = Field(default=0, ge=0)


class ProjectPermissionGrant(_StrictProjectModel):
    grant_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    issued_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    destructive_allowed: bool = False
    max_uses: int | None = Field(default=None, ge=1)
    uses: int = Field(default=0, ge=0)
    reason: str | None = None

    @model_validator(mode="after")
    def _validate_lifetime_and_usage(self) -> "ProjectPermissionGrant":
        if self.expires_at_ms <= self.issued_at_ms:
            raise ValueError("expires_at_ms must be greater than issued_at_ms")
        if self.max_uses is not None and self.uses > self.max_uses:
            raise ValueError("uses must not exceed max_uses")
        return self


class ProjectBudgetPolicy(_StrictProjectModel):
    max_iterations: int = Field(default=0, ge=0)
    max_wall_clock_ms: int = Field(default=0, ge=0)
    max_tool_calls: int = Field(default=0, ge=0)
    max_tokens: int = Field(default=0, ge=0)
    unattended: bool = False
    destructive_requires_confirmation: bool = True


class ProjectPolicyState(_StrictProjectModel):
    task_id: str = Field(min_length=1)
    project_run_id: str = Field(min_length=1)
    grants: tuple[ProjectPermissionGrant, ...] = ()
    denied_tool_names: tuple[str, ...] = ()
    budget: ProjectBudgetPolicy = Field(default_factory=ProjectBudgetPolicy)
    updated_at_ms: int = Field(default_factory=now_ms, ge=0)
    version: int = Field(default=1, ge=1)


class ProjectPermissionCheckResult(_StrictProjectModel):
    decision: ProjectPermissionDecision
    tool_name: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    grant_id: str | None = None
    reason: str
    expires_at_ms: int | None = Field(default=None, ge=0)

    @property
    def allowed(self) -> bool:
        return self.decision == ProjectPermissionDecision.ALLOWED


class ProjectBudgetCheckResult(_StrictProjectModel):
    decision: ProjectPermissionDecision
    reason: str
    limits: dict[str, int] = Field(default_factory=dict)
    used: dict[str, int] = Field(default_factory=dict)
    remaining: dict[str, int] = Field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision == ProjectPermissionDecision.ALLOWED


__all__ = [
    "ProjectBudgetCheckResult",
    "ProjectBudgetPolicy",
    "ProjectCheckpoint",
    "ProjectControlAction",
    "ProjectControlResult",
    "ProjectCycleDecision",
    "ProjectCycleRecord",
    "ProjectObjectiveContract",
    "ProjectObjectiveLedger",
    "ProjectPermissionCheckResult",
    "ProjectPermissionDecision",
    "ProjectPermissionGrant",
    "ProjectPolicyState",
    "ProjectRun",
    "ProjectVerificationState",
]
