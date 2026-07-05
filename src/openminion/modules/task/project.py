from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openminion.modules.task.autonomy import (
    AutonomyRun,
    AutonomyRunPhase,
    AutonomyRunStatus,
    now_ms,
)
from openminion.modules.task.runtime.lifecycle import (
    TaskLifecycleRecord,
    TaskLifecycleState,
    TaskManager,
)


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


_OPEN_PROJECT_TASK_STATES = {
    TaskLifecycleState.ACTIVE,
    TaskLifecycleState.PAUSED,
}

_PROJECT_POLICY_METADATA_KEY = "project_policy"


def build_project_run_projection(
    autonomy_run: AutonomyRun,
    *,
    objective_ledger_ref: str,
    evidence_ledger_ref: str,
    resume_packet_ref: str,
    operator_decision_log_ref: str,
    capability_plan_ref: str,
    metrics_summary_ref: str,
    task_record: TaskLifecycleRecord | None = None,
    project_run_id: str | None = None,
    verification_state: ProjectVerificationState = (
        ProjectVerificationState.NOT_STARTED
    ),
    next_wakeup_at_ms: int | None = None,
    blocked_reason: str | None = None,
) -> ProjectRun:
    task_id = autonomy_run.task_id
    goal_id = autonomy_run.goal_id
    workspace_ref = autonomy_run.workspace_ref
    if not task_id:
        raise ValueError("project run projection requires autonomy_run.task_id")
    if not goal_id:
        raise ValueError("project run projection requires autonomy_run.goal_id")
    if not workspace_ref:
        raise ValueError("project run projection requires autonomy_run.workspace_ref")
    if task_record is not None and task_record.task_id != task_id:
        raise ValueError("task_record.task_id must match autonomy_run.task_id")

    return ProjectRun(
        project_run_id=project_run_id or f"prun_{autonomy_run.run_id}",
        autonomy_run_id=autonomy_run.run_id,
        task_id=task_id,
        goal_id=goal_id,
        objective_ledger_ref=objective_ledger_ref,
        evidence_ledger_ref=evidence_ledger_ref,
        resume_packet_ref=resume_packet_ref,
        operator_decision_log_ref=operator_decision_log_ref,
        capability_plan_ref=capability_plan_ref,
        metrics_summary_ref=metrics_summary_ref,
        workspace_ref=workspace_ref,
        status=autonomy_run.status,
        phase=autonomy_run.phase,
        created_at_ms=autonomy_run.created_at_ms,
        updated_at_ms=autonomy_run.updated_at_ms,
        last_checkpoint_id=autonomy_run.checkpoint_id,
        next_wakeup_at_ms=next_wakeup_at_ms,
        blocked_reason=blocked_reason
        or (autonomy_run.last_error.message if autonomy_run.last_error else None),
        verification_state=verification_state,
        task_state=task_record.state if task_record is not None else None,
    )


def find_open_project_worker(
    task_manager: TaskManager,
    *,
    project_run_id: str,
    exclude_task_id: str | None = None,
) -> TaskLifecycleRecord | None:
    normalized_project_id = str(project_run_id or "").strip()
    if not normalized_project_id:
        raise ValueError("project_run_id is required")
    excluded = str(exclude_task_id or "").strip() or None
    for record in task_manager.lifecycle_repository.list(limit=1000):
        if record.state not in _OPEN_PROJECT_TASK_STATES:
            continue
        if excluded is not None and record.task_id == excluded:
            continue
        if str(record.metadata.get("project_run_id") or "") == normalized_project_id:
            return record
    return None


def link_project_run_to_task(
    task_manager: TaskManager,
    project_run: ProjectRun,
) -> TaskLifecycleRecord:
    record = task_manager.get_task(project_run.task_id)
    if record is None:
        raise KeyError(f"task not found: {project_run.task_id}")
    if record.state not in _OPEN_PROJECT_TASK_STATES:
        raise ValueError("project run can only link to an active or paused task")
    existing = find_open_project_worker(
        task_manager,
        project_run_id=project_run.project_run_id,
        exclude_task_id=project_run.task_id,
    )
    if existing is not None:
        raise ValueError(
            "open project worker already exists: "
            f"{project_run.project_run_id} on task {existing.task_id}"
        )

    metadata = dict(record.metadata)
    metadata.update(
        {
            "project_run_id": project_run.project_run_id,
            "autonomy_run_id": project_run.autonomy_run_id,
            "goal_id": project_run.goal_id,
            "project_status": project_run.status.value,
            "project_phase": project_run.phase.value,
            "verification_state": project_run.verification_state.value,
        }
    )
    return task_manager.update_task_metadata(
        task_id=project_run.task_id,
        metadata=metadata,
    )


def save_project_run_checkpoint(
    task_manager: TaskManager,
    project_run: ProjectRun,
    *,
    checkpoint_id: str,
    payload: dict[str, object] | None = None,
) -> ProjectCheckpoint:
    link_project_run_to_task(task_manager, project_run)
    checkpoint = ProjectCheckpoint(
        checkpoint_id=checkpoint_id,
        project_run=project_run.model_copy(
            update={"last_checkpoint_id": checkpoint_id}
        ),
        payload=dict(payload or {}),
    )
    task_manager.save_checkpoint(
        project_run.task_id,
        checkpoint_id,
        checkpoint.model_dump(mode="json"),
    )
    return checkpoint


def load_latest_project_checkpoint(
    task_manager: TaskManager,
    *,
    task_id: str,
) -> ProjectCheckpoint | None:
    latest = task_manager.get_latest_checkpoint(task_id)
    if latest is None:
        return None
    _checkpoint_id, state = latest
    if state.get("kind") != "project_run":
        raise ValueError("latest checkpoint is not a project_run checkpoint")
    return ProjectCheckpoint.model_validate(state)


def resume_project_run_from_latest_checkpoint(
    task_manager: TaskManager,
    *,
    task_id: str,
) -> ProjectRun:
    checkpoint = load_latest_project_checkpoint(task_manager, task_id=task_id)
    if checkpoint is None:
        raise KeyError(f"project checkpoint not found: {task_id}")
    record = task_manager.get_task(task_id)
    if record is None:
        raise KeyError(f"task not found: {task_id}")
    metadata = dict(record.metadata)
    metadata["resume_count"] = int(metadata.get("resume_count") or 0) + 1
    metadata["last_resume_checkpoint_id"] = checkpoint.checkpoint_id
    task_manager.update_task_metadata(task_id=task_id, metadata=metadata)
    return checkpoint.project_run


def record_project_cycle(
    task_manager: TaskManager,
    project_run: ProjectRun,
    *,
    cycle_id: str,
    milestone: str,
    intended_action: str,
    evidence_refs: tuple[str, ...],
    validation_refs: tuple[str, ...],
    decision: ProjectCycleDecision,
    decision_reason: str | None = None,
    checkpoint_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> ProjectCycleRecord:
    normalized_reason = str(decision_reason or "").strip() or None
    if decision != ProjectCycleDecision.CONTINUE and normalized_reason is None:
        raise ValueError("decision_reason is required unless decision is continue")
    effective_checkpoint_id = (
        str(checkpoint_id or "").strip()
        or f"{project_run.project_run_id}:{str(cycle_id).strip()}"
    )
    record = ProjectCycleRecord(
        cycle_id=cycle_id,
        project_run_id=project_run.project_run_id,
        task_id=project_run.task_id,
        goal_id=project_run.goal_id,
        milestone=milestone,
        intended_action=intended_action,
        evidence_refs=evidence_refs,
        validation_refs=validation_refs,
        checkpoint_id=effective_checkpoint_id,
        decision=decision,
        decision_reason=normalized_reason,
        created_at_ms=now_ms(),
    )
    checkpoint_payload: dict[str, object] = {
        "cycle": record.model_dump(mode="json"),
    }
    if payload:
        checkpoint_payload["payload"] = dict(payload)
    save_project_run_checkpoint(
        task_manager,
        project_run,
        checkpoint_id=effective_checkpoint_id,
        payload=checkpoint_payload,
    )
    return record


def replay_project_cycles(
    task_manager: TaskManager,
    *,
    task_id: str,
) -> tuple[ProjectCycleRecord, ...]:
    cycles: list[ProjectCycleRecord] = []
    for checkpoint_id in task_manager.list_checkpoints(task_id):
        state = task_manager.get_checkpoint(task_id, checkpoint_id)
        if not state or state.get("kind") != "project_run":
            continue
        checkpoint = ProjectCheckpoint.model_validate(state)
        raw_cycle = checkpoint.payload.get("cycle")
        if isinstance(raw_cycle, dict):
            cycles.append(ProjectCycleRecord.model_validate(raw_cycle))
    return tuple(cycles)


def load_project_policy_state(
    task_manager: TaskManager,
    *,
    task_id: str,
) -> ProjectPolicyState | None:
    record = task_manager.get_task(task_id)
    if record is None:
        raise KeyError(f"task not found: {task_id}")
    raw = record.metadata.get(_PROJECT_POLICY_METADATA_KEY)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("project policy metadata must be an object")
    return ProjectPolicyState.model_validate(raw)


def save_project_policy_state(
    task_manager: TaskManager,
    policy_state: ProjectPolicyState,
) -> ProjectPolicyState:
    record = task_manager.get_task(policy_state.task_id)
    if record is None:
        raise KeyError(f"task not found: {policy_state.task_id}")
    metadata = dict(record.metadata)
    refreshed = policy_state.model_copy(update={"updated_at_ms": now_ms()})
    metadata[_PROJECT_POLICY_METADATA_KEY] = refreshed.model_dump(mode="json")
    task_manager.update_task_metadata(
        task_id=policy_state.task_id,
        metadata=metadata,
    )
    return refreshed


def build_project_policy_state(
    task_manager: TaskManager,
    *,
    task_id: str,
    budget: ProjectBudgetPolicy | None = None,
    denied_tool_names: tuple[str, ...] = (),
) -> ProjectPolicyState:
    record = task_manager.get_task(task_id)
    if record is None:
        raise KeyError(f"task not found: {task_id}")
    project_run_id = str(record.metadata.get("project_run_id") or "").strip()
    if not project_run_id:
        raise ValueError("task is not linked to a project run")
    return ProjectPolicyState(
        task_id=task_id,
        project_run_id=project_run_id,
        denied_tool_names=tuple(
            sorted({name.strip() for name in denied_tool_names if name.strip()})
        ),
        budget=budget or ProjectBudgetPolicy(),
    )


def issue_project_permission_grant(
    task_manager: TaskManager,
    *,
    task_id: str,
    grant_id: str,
    tool_name: str,
    scope: str,
    expires_at_ms: int,
    destructive_allowed: bool = False,
    max_uses: int | None = None,
    reason: str | None = None,
    issued_at_ms: int | None = None,
) -> ProjectPolicyState:
    state = load_project_policy_state(task_manager, task_id=task_id)
    if state is None:
        state = build_project_policy_state(task_manager, task_id=task_id)
    issued = now_ms() if issued_at_ms is None else int(issued_at_ms)
    grant = ProjectPermissionGrant(
        grant_id=grant_id,
        tool_name=_normalize_project_tool_name(tool_name),
        scope=str(scope or "").strip(),
        issued_at_ms=issued,
        expires_at_ms=expires_at_ms,
        destructive_allowed=destructive_allowed,
        max_uses=max_uses,
        reason=str(reason or "").strip() or None,
    )
    grants = tuple(
        existing for existing in state.grants if existing.grant_id != grant.grant_id
    ) + (grant,)
    return save_project_policy_state(
        task_manager,
        state.model_copy(update={"grants": grants}),
    )


def evaluate_project_permission(
    task_manager: TaskManager,
    *,
    task_id: str,
    tool_name: str,
    scope: str,
    destructive: bool = False,
    at_ms: int | None = None,
) -> ProjectPermissionCheckResult:
    normalized_tool = _normalize_project_tool_name(tool_name)
    normalized_scope = str(scope or "").strip()
    if not normalized_scope:
        raise ValueError("scope is required")
    state = load_project_policy_state(task_manager, task_id=task_id)
    if state is None:
        return ProjectPermissionCheckResult(
            decision=ProjectPermissionDecision.APPROVAL_REQUIRED,
            tool_name=normalized_tool,
            scope=normalized_scope,
            reason="project policy state is not configured",
        )
    denied = {_normalize_project_tool_name(name) for name in state.denied_tool_names}
    if normalized_tool in denied:
        return ProjectPermissionCheckResult(
            decision=ProjectPermissionDecision.DENIED,
            tool_name=normalized_tool,
            scope=normalized_scope,
            reason="tool is denied by project policy",
        )

    now = now_ms() if at_ms is None else int(at_ms)
    for grant in state.grants:
        if grant.tool_name != normalized_tool or grant.scope != normalized_scope:
            continue
        if now >= grant.expires_at_ms:
            return ProjectPermissionCheckResult(
                decision=ProjectPermissionDecision.EXPIRED,
                tool_name=normalized_tool,
                scope=normalized_scope,
                grant_id=grant.grant_id,
                reason="grant expired",
                expires_at_ms=grant.expires_at_ms,
            )
        if grant.max_uses is not None and grant.uses >= grant.max_uses:
            return ProjectPermissionCheckResult(
                decision=ProjectPermissionDecision.EXPIRED,
                tool_name=normalized_tool,
                scope=normalized_scope,
                grant_id=grant.grant_id,
                reason="grant use limit exhausted",
                expires_at_ms=grant.expires_at_ms,
            )
        if destructive and (
            state.budget.destructive_requires_confirmation
            and not grant.destructive_allowed
        ):
            return ProjectPermissionCheckResult(
                decision=ProjectPermissionDecision.DENIED,
                tool_name=normalized_tool,
                scope=normalized_scope,
                grant_id=grant.grant_id,
                reason="destructive action requires an explicit destructive grant",
                expires_at_ms=grant.expires_at_ms,
            )
        return ProjectPermissionCheckResult(
            decision=ProjectPermissionDecision.ALLOWED,
            tool_name=normalized_tool,
            scope=normalized_scope,
            grant_id=grant.grant_id,
            reason="matched project permission grant",
            expires_at_ms=grant.expires_at_ms,
        )

    return ProjectPermissionCheckResult(
        decision=ProjectPermissionDecision.APPROVAL_REQUIRED,
        tool_name=normalized_tool,
        scope=normalized_scope,
        reason="no matching project permission grant",
    )


def consume_project_permission_grant(
    task_manager: TaskManager,
    *,
    task_id: str,
    grant_id: str,
) -> ProjectPolicyState:
    state = load_project_policy_state(task_manager, task_id=task_id)
    if state is None:
        raise KeyError(f"project policy state not found: {task_id}")
    grants: list[ProjectPermissionGrant] = []
    found = False
    for grant in state.grants:
        if grant.grant_id != grant_id:
            grants.append(grant)
            continue
        found = True
        if grant.max_uses is not None and grant.uses >= grant.max_uses:
            raise ValueError("project permission grant use limit exhausted")
        grants.append(grant.model_copy(update={"uses": grant.uses + 1}))
    if not found:
        raise KeyError(f"project permission grant not found: {grant_id}")
    return save_project_policy_state(
        task_manager,
        state.model_copy(update={"grants": tuple(grants)}),
    )


def evaluate_project_budget(
    task_manager: TaskManager,
    *,
    task_id: str,
    iterations: int = 0,
    wall_clock_ms: int = 0,
    tool_calls: int = 0,
    tokens: int = 0,
) -> ProjectBudgetCheckResult:
    state = load_project_policy_state(task_manager, task_id=task_id)
    if state is None:
        return ProjectBudgetCheckResult(
            decision=ProjectPermissionDecision.APPROVAL_REQUIRED,
            reason="project budget policy is not configured",
        )
    record = task_manager.get_task(task_id)
    if record is None:
        raise KeyError(f"task not found: {task_id}")
    limits = _project_budget_limits_with_extensions(state.budget, record.metadata)
    used = {
        "iterations": max(0, int(iterations)),
        "wall_clock_ms": max(0, int(wall_clock_ms)),
        "tool_calls": max(0, int(tool_calls)),
        "tokens": max(0, int(tokens)),
    }
    remaining = {
        key: max(0, limit - used[key]) for key, limit in limits.items() if limit > 0
    }
    for key, limit in limits.items():
        if limit > 0 and used[key] > limit:
            return ProjectBudgetCheckResult(
                decision=ProjectPermissionDecision.BUDGET_EXCEEDED,
                reason=f"{key} budget exceeded",
                limits=limits,
                used=used,
                remaining=remaining,
            )
    return ProjectBudgetCheckResult(
        decision=ProjectPermissionDecision.ALLOWED,
        reason="within project budget policy",
        limits=limits,
        used=used,
        remaining=remaining,
    )


def _normalize_project_tool_name(tool_name: str) -> str:
    normalized = str(tool_name or "").strip().lower()
    if not normalized:
        raise ValueError("tool_name is required")
    return normalized


def _project_budget_limits_with_extensions(
    budget: ProjectBudgetPolicy,
    metadata: dict[str, object],
) -> dict[str, int]:
    extensions = (
        metadata.get("budget_extensions")
        if isinstance(metadata.get("budget_extensions"), dict)
        else {}
    )
    return {
        "iterations": int(budget.max_iterations)
        + _metadata_int(extensions, "extra_iterations"),
        "wall_clock_ms": int(budget.max_wall_clock_ms)
        + _metadata_int(extensions, "extra_wall_clock_ms"),
        "tool_calls": int(budget.max_tool_calls)
        + _metadata_int(extensions, "extra_tool_calls"),
        "tokens": int(budget.max_tokens),
    }


def _metadata_int(metadata: object, key: str) -> int:
    if not isinstance(metadata, dict):
        return 0
    try:
        return max(0, int(metadata.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def apply_project_control(
    task_manager: TaskManager,
    *,
    task_id: str,
    action: ProjectControlAction,
    priority: str | None = None,
    input_request_id: str | None = None,
    answer: str | None = None,
    extra_iterations: int = 0,
    extra_wall_clock_ms: int = 0,
    extra_tool_calls: int = 0,
) -> ProjectControlResult:
    record = task_manager.get_task(task_id)
    if record is None:
        raise KeyError(f"task not found: {task_id}")

    if (
        action == ProjectControlAction.PAUSE
        and record.state == TaskLifecycleState.ACTIVE
    ):
        record = task_manager.transition_task(
            task_id=record.task_id,
            to_state=TaskLifecycleState.PAUSED,
        )
    elif (
        action == ProjectControlAction.RESUME
        and record.state == TaskLifecycleState.PAUSED
    ):
        record = task_manager.transition_task(
            task_id=record.task_id,
            to_state=TaskLifecycleState.ACTIVE,
        )
    elif action == ProjectControlAction.CANCEL:
        record = task_manager.transition_task(
            task_id=record.task_id,
            to_state=TaskLifecycleState.CANCELLED,
        )
    elif action == ProjectControlAction.REPRIORITIZE:
        normalized_priority = str(priority or "").strip()
        if not normalized_priority:
            raise ValueError("priority is required for reprioritize")
        metadata = dict(record.metadata)
        metadata["priority"] = normalized_priority
        record = task_manager.update_task_metadata(
            task_id=record.task_id,
            metadata=metadata,
        )
    elif action == ProjectControlAction.ANSWER_INPUT:
        request_id = str(input_request_id or "").strip()
        normalized_answer = str(answer or "").strip()
        if not request_id:
            raise ValueError("input_request_id is required for answer-input-request")
        if not normalized_answer:
            raise ValueError("answer is required for answer-input-request")
        metadata = dict(record.metadata)
        answers = list(metadata.get("operator_answers", []) or [])
        answers.append({"request_id": request_id, "answer": normalized_answer})
        metadata["operator_answers"] = answers
        record = task_manager.update_task_metadata(
            task_id=record.task_id,
            metadata=metadata,
        )
    elif action == ProjectControlAction.EXTEND_BUDGET:
        if extra_iterations < 1 and extra_wall_clock_ms < 1 and extra_tool_calls < 1:
            raise ValueError("extend-budget requires a positive budget delta")
        metadata = dict(record.metadata)
        current = dict(metadata.get("budget_extensions", {}) or {})
        current["extra_iterations"] = int(current.get("extra_iterations") or 0) + max(
            0, int(extra_iterations)
        )
        current["extra_wall_clock_ms"] = int(
            current.get("extra_wall_clock_ms") or 0
        ) + max(0, int(extra_wall_clock_ms))
        current["extra_tool_calls"] = int(current.get("extra_tool_calls") or 0) + max(
            0, int(extra_tool_calls)
        )
        metadata["budget_extensions"] = current
        record = task_manager.update_task_metadata(
            task_id=record.task_id,
            metadata=metadata,
        )

    return build_project_control_result(task_manager, record, action=action)


def build_project_control_result(
    task_manager: TaskManager,
    record: TaskLifecycleRecord,
    *,
    action: ProjectControlAction,
) -> ProjectControlResult:
    cycles = replay_project_cycles(task_manager, task_id=record.task_id)
    metadata = record.metadata
    operator_answers = list(metadata.get("operator_answers", []) or [])
    return ProjectControlResult(
        action=action,
        task_id=record.task_id,
        state=record.state,
        project_run_id=str(metadata.get("project_run_id") or "") or None,
        autonomy_run_id=str(metadata.get("autonomy_run_id") or "") or None,
        goal_id=str(metadata.get("goal_id") or "") or None,
        last_checkpoint_id=str(metadata.get("last_checkpoint_id") or "") or None,
        resume_count=int(metadata.get("resume_count") or 0),
        priority=str(metadata.get("priority") or "") or None,
        operator_answer_count=len(operator_answers),
        budget_extensions={
            str(key): int(value)
            for key, value in dict(metadata.get("budget_extensions", {}) or {}).items()
        },
        cycle_count=len(cycles),
    )


def render_project_run_summary(project_run: ProjectRun) -> str:
    lines = [
        f"project_run_id: {project_run.project_run_id}",
        f"autonomy_run_id: {project_run.autonomy_run_id}",
        f"task_id: {project_run.task_id}",
        f"goal_id: {project_run.goal_id}",
        f"status: {project_run.status.value}",
        f"phase: {project_run.phase.value}",
        f"verification: {project_run.verification_state.value}",
        f"workspace: {project_run.workspace_ref}",
    ]
    if project_run.last_checkpoint_id:
        lines.append(f"checkpoint: {project_run.last_checkpoint_id}")
    if project_run.blocked_reason:
        lines.append(f"blocked_reason: {project_run.blocked_reason}")
    return "\n".join(lines)


def render_project_control_result(result: ProjectControlResult) -> str:
    lines = [
        f"task_id: {result.task_id}",
        f"action: {result.action.value}",
        f"state: {result.state.value}",
        f"project_run_id: {result.project_run_id or '-'}",
        f"goal_id: {result.goal_id or '-'}",
        f"checkpoint: {result.last_checkpoint_id or '-'}",
        f"cycles: {result.cycle_count}",
        f"resume_count: {result.resume_count}",
    ]
    if result.priority:
        lines.append(f"priority: {result.priority}")
    if result.operator_answer_count:
        lines.append(f"operator_answers: {result.operator_answer_count}")
    if result.budget_extensions:
        lines.append(f"budget_extensions: {result.budget_extensions}")
    return "\n".join(lines)


__all__ = [
    "ProjectControlAction",
    "ProjectControlResult",
    "ProjectBudgetCheckResult",
    "ProjectBudgetPolicy",
    "ProjectCheckpoint",
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
    "apply_project_control",
    "build_project_control_result",
    "build_project_policy_state",
    "build_project_run_projection",
    "consume_project_permission_grant",
    "evaluate_project_budget",
    "evaluate_project_permission",
    "find_open_project_worker",
    "issue_project_permission_grant",
    "link_project_run_to_task",
    "load_latest_project_checkpoint",
    "load_project_policy_state",
    "record_project_cycle",
    "render_project_control_result",
    "render_project_run_summary",
    "replay_project_cycles",
    "resume_project_run_from_latest_checkpoint",
    "save_project_policy_state",
    "save_project_run_checkpoint",
]
