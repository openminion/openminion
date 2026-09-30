from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openminion.base.errors import error_info_from_exception
from openminion.modules.task.autonomy import (
    AutonomyRun,
    AutonomyRunError,
    AutonomyRunPhase,
    AutonomyRunStatus,
    AutonomyRunStore,
    TestEvidence,
    build_local_workspace_ref,
    now_ms,
)
from openminion.modules.task.runtime.lifecycle import TaskLifecycleState, TaskManager

from . import checkpoints as project_checkpoints
from .constants import REPOSITORY_LIFECYCLE_PAYLOAD_KEY
from .models import (
    ProjectCheckpoint,
    ProjectCycleDecision,
    ProjectRun,
    ProjectVerificationState,
)
from .verification import (
    ProjectDomainVerificationStatus,
    write_project_terminal_proof,
)

if TYPE_CHECKING:
    from .turn import ProjectTurnResult


class AutonomyLoopConditionKind(StrEnum):
    PRODUCTIVE = "productive"
    WAITING = "waiting"
    RETRYABLE_FAILURE = "retryable_failure"
    MISSING_CAPABILITY = "missing_capability"
    DENIED = "denied"
    DUPLICATE_ACTION = "duplicate_action"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    STRATEGY_FAILURE = "strategy_failure"
    TERMINAL_INABILITY = "terminal_inability"
    CANCELLED = "cancelled"


class AutonomyLoopJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: AutonomyLoopConditionKind
    run_status: AutonomyRunStatus
    requires_model_replan: bool = False
    requires_operator: bool = False
    bounded_retry_allowed: bool = False
    terminal: bool = False
    reason_code: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    next_resume_action: str | None = None

    @model_validator(mode="after")
    def _operator_cases_need_resume_action(self) -> "AutonomyLoopJudgment":
        if self.requires_operator and not self.next_resume_action:
            raise ValueError("operator-required judgment needs next_resume_action")
        return self


def terminal_checkpoint_projection(
    run: AutonomyRun,
    checkpoint: ProjectCheckpoint,
) -> tuple[AutonomyRun, ProjectCycleDecision, tuple[TestEvidence, ...]] | None:
    project_run = checkpoint.project_run
    if (
        project_run.status == AutonomyRunStatus.BLOCKED
        and run.status == AutonomyRunStatus.RUNNING
        and run.checkpoint_id == checkpoint.checkpoint_id
    ):
        return None
    if project_run.status not in {
        AutonomyRunStatus.BLOCKED,
        AutonomyRunStatus.FAILED,
        AutonomyRunStatus.COMPLETED,
        AutonomyRunStatus.CANCELLED,
    }:
        return None
    raw_verification = checkpoint.payload.get("verification")
    verification = (
        tuple(
            TestEvidence.model_validate(item)
            for item in raw_verification
            if isinstance(item, Mapping)
        )
        if isinstance(raw_verification, list)
        else ()
    )
    projected = run.model_copy(
        update={
            "checkpoint_id": checkpoint.checkpoint_id,
            "workspace_ref": project_run.workspace_ref,
            "status": project_run.status,
            "phase": project_run.phase,
            "updated_at_ms": project_run.updated_at_ms,
            "completed_at_ms": (
                project_run.updated_at_ms
                if project_run.status == AutonomyRunStatus.COMPLETED
                else run.completed_at_ms
            ),
        }
    )
    decision = checkpoint_decision(checkpoint.payload)
    return projected, decision, verification


def recover_terminal_checkpoint(
    *,
    task_manager: TaskManager,
    autonomy_store: AutonomyRunStore,
    run: AutonomyRun,
    checkpoint: ProjectCheckpoint,
    cycle_summaries: tuple[str, ...],
    workspace: Path,
) -> (
    tuple[
        AutonomyRun,
        ProjectCheckpoint,
        ProjectCycleDecision,
        tuple[TestEvidence, ...],
    ]
    | None
):
    projection = terminal_checkpoint_projection(run, checkpoint)
    if projection is None:
        return None
    terminal_run, decision, verification = projection
    if checkpoint.payload.get("proof_recovery_failed"):
        blocked = run.model_copy(
            update={
                "checkpoint_id": checkpoint.checkpoint_id,
                "workspace_ref": checkpoint.project_run.workspace_ref,
                "status": AutonomyRunStatus.BLOCKED,
                "phase": AutonomyRunPhase.CLOSED,
                "operator_summary": "Terminal proof recovery is blocked.",
                "next_action_hint": "Restore proof storage, then resume the project.",
                "updated_at_ms": checkpoint.project_run.updated_at_ms,
            }
        )
        autonomy_store.save(blocked)
        transition_project_task(
            task_manager,
            checkpoint.project_run.task_id,
            decision=ProjectCycleDecision.BLOCKED,
            status=AutonomyRunStatus.BLOCKED,
        )
        return blocked, checkpoint, ProjectCycleDecision.BLOCKED, verification
    if not terminal_run.proof_packet_ref:
        try:
            terminal_run = write_project_terminal_proof(
                autonomy_store,
                terminal_run,
                checkpoint.project_run,
                verification=verification,
                cycle_summaries=cycle_summaries,
                workspace=workspace,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            blocked, blocked_checkpoint = block_terminal_proof_recovery(
                task_manager=task_manager,
                autonomy_store=autonomy_store,
                run=run,
                checkpoint=checkpoint,
                error=exc,
            )
            return (
                blocked,
                blocked_checkpoint,
                ProjectCycleDecision.BLOCKED,
                verification,
            )
    autonomy_store.save(terminal_run)
    transition_project_task(
        task_manager,
        checkpoint.project_run.task_id,
        decision=decision,
        status=checkpoint.project_run.status,
    )
    return terminal_run, checkpoint, decision, verification


def block_terminal_proof_recovery(
    *,
    task_manager: TaskManager,
    autonomy_store: AutonomyRunStore,
    run: AutonomyRun,
    checkpoint: ProjectCheckpoint,
    error: Exception,
) -> tuple[AutonomyRun, ProjectCheckpoint]:
    timestamp = now_ms()
    info = error_info_from_exception(
        error,
        default_code="terminal_proof_recovery_failed",
        default_message="Terminal proof recovery failed.",
        namespace="task.project",
    )
    project_run = checkpoint.project_run.model_copy(
        update={
            "updated_at_ms": timestamp,
            "task_state": TaskLifecycleState.PAUSED,
            "next_wake_job_id": None,
        }
    )
    blocked_checkpoint = project_checkpoints.save_project_run_checkpoint(
        task_manager,
        project_run,
        checkpoint_id=f"{project_run.project_run_id}:proof-recovery-blocked:{timestamp}",
        payload={
            **checkpoint.payload,
            "decision_reason": "terminal_proof_recovery_failed",
            "proof_recovery_failed": True,
            "error": info.to_dict(),
        },
    )
    blocked = run.model_copy(
        update={
            "checkpoint_id": blocked_checkpoint.checkpoint_id,
            "workspace_ref": project_run.workspace_ref,
            "status": AutonomyRunStatus.BLOCKED,
            "phase": AutonomyRunPhase.CLOSED,
            "operator_summary": info.message,
            "next_action_hint": "Restore proof storage, then resume the project.",
            "last_error": AutonomyRunError(code=info.code, message=info.message),
            "updated_at_ms": timestamp,
        }
    )
    autonomy_store.save(blocked)
    transition_project_task(
        task_manager,
        project_run.task_id,
        decision=ProjectCycleDecision.BLOCKED,
        status=AutonomyRunStatus.BLOCKED,
    )
    return blocked, blocked_checkpoint


def checkpoint_decision(payload: Mapping[str, object]) -> ProjectCycleDecision:
    return ProjectCycleDecision(str(payload.get("decision") or "blocked"))


def record_project_cycle_interruption(
    *,
    task_manager: TaskManager,
    autonomy_store: AutonomyRunStore,
    run: AutonomyRun,
    checkpoint: ProjectCheckpoint,
    claim: object,
    error: Exception,
    triggering_cron_job_id: str | None,
) -> None:
    from .turn import ProjectTurnResult, project_workspace

    timestamp = now_ms()
    error_info = error_info_from_exception(
        error,
        default_code="project_cycle_interrupted",
        default_message="Project cycle was interrupted before a checkpoint was committed.",
        namespace="task.project",
    )
    failure = AutonomyRunError(code=error_info.code, message=error_info.message)
    checkpoint_id = (
        f"{checkpoint.project_run.project_run_id}:interrupted:{uuid4().hex[:12]}"
    )
    workspace_ref = build_local_workspace_ref(
        project_workspace(checkpoint.project_run.workspace_ref)
    )
    interrupted_project = checkpoint.project_run.model_copy(
        update={
            "workspace_ref": workspace_ref,
            "status": AutonomyRunStatus.BLOCKED,
            "phase": AutonomyRunPhase.RECOVER,
            "updated_at_ms": timestamp,
            "blocked_reason": failure.message,
            "verification_state": ProjectVerificationState.BLOCKED,
            "task_state": TaskLifecycleState.PAUSED,
            "next_wake_job_id": None,
        }
    )
    repository_payload = project_checkpoints.advance_repository_lifecycle_payload(
        checkpoint,
        interrupted_project,
        turn=ProjectTurnResult(summary=failure.message),
        verification_count=0,
        next_action=ProjectCycleDecision.BLOCKED.value,
    )
    committed = project_checkpoints.commit_project_run_checkpoint(
        task_manager,
        interrupted_project,
        claim=claim,
        checkpoint_id=checkpoint_id,
        triggering_cron_job_id=triggering_cron_job_id,
        payload={
            **checkpoint.payload,
            **repository_payload,
            "decision": ProjectCycleDecision.BLOCKED.value,
            "decision_reason": failure.code,
            "error": failure.model_dump(mode="json"),
        },
    )
    autonomy_store.save(
        run.model_copy(
            update={
                "checkpoint_id": committed.checkpoint_id,
                "workspace_ref": workspace_ref,
                "status": AutonomyRunStatus.BLOCKED,
                "phase": AutonomyRunPhase.RECOVER,
                "operator_summary": failure.message,
                "next_action_hint": "Resume the project to retry the interrupted cycle.",
                "last_error": failure,
                "updated_at_ms": timestamp,
            }
        )
    )
    task_manager.transition_task(
        task_id=checkpoint.project_run.task_id,
        to_state=TaskLifecycleState.PAUSED,
    )


def record_project_cycle_interruption_if_current(
    *,
    task_manager: TaskManager,
    autonomy_store: AutonomyRunStore,
    run: AutonomyRun,
    checkpoint: ProjectCheckpoint,
    claim: object,
    error: Exception,
    triggering_cron_job_id: str | None,
) -> None:
    latest = project_checkpoints.load_latest_project_checkpoint(
        task_manager,
        task_id=checkpoint.project_run.task_id,
    )
    if latest is None or latest.checkpoint_id != checkpoint.checkpoint_id:
        return
    record_project_cycle_interruption(
        task_manager=task_manager,
        autonomy_store=autonomy_store,
        run=run,
        checkpoint=checkpoint,
        claim=claim,
        error=error,
        triggering_cron_job_id=triggering_cron_job_id,
    )


def reconcile_run_projection(
    run: AutonomyRun,
    project_run: ProjectRun,
    *,
    autonomy_store: AutonomyRunStore,
) -> AutonomyRun:
    if (
        run.checkpoint_id == project_run.last_checkpoint_id
        and run.status == project_run.status
    ):
        return run
    resumed = (
        run.status == AutonomyRunStatus.RUNNING
        and run.checkpoint_id == project_run.last_checkpoint_id
    )
    reconciled = run.model_copy(
        update={
            "checkpoint_id": project_run.last_checkpoint_id,
            "workspace_ref": project_run.workspace_ref,
            "status": run.status if resumed else project_run.status,
            "phase": run.phase if resumed else project_run.phase,
            "updated_at_ms": project_run.updated_at_ms,
        }
    )
    autonomy_store.save(reconciled)
    return reconciled


def transition_project_task(
    task_manager: TaskManager,
    task_id: str,
    *,
    decision: ProjectCycleDecision,
    status: AutonomyRunStatus,
) -> None:
    target = None
    if status == AutonomyRunStatus.FAILED:
        target = TaskLifecycleState.FAILED
    elif status == AutonomyRunStatus.CANCELLED:
        target = TaskLifecycleState.CANCELLED
    elif decision == ProjectCycleDecision.STOP:
        target = TaskLifecycleState.DONE
    elif decision in {
        ProjectCycleDecision.BLOCKED,
        ProjectCycleDecision.NEEDS_INPUT,
    }:
        target = TaskLifecycleState.PAUSED
    if target is not None:
        task_manager.transition_task(task_id=task_id, to_state=target)


def block_unconfigured_project_domain(
    *,
    task_manager: TaskManager,
    autonomy_store: AutonomyRunStore,
    run: AutonomyRun,
    project_run: ProjectRun,
) -> AutonomyRun:
    domain = run.execution_selectors.verification_domain
    blocked = run.model_copy(
        update={
            "status": AutonomyRunStatus.BLOCKED,
            "phase": AutonomyRunPhase.CLOSED,
            "operator_summary": "Project domain is not configured.",
            "next_action_hint": "Start a coding or research project.",
            "last_error": AutonomyRunError(
                code="project_domain_not_configured",
                message=f"Project domain is not configured: {domain}",
            ),
            "updated_at_ms": now_ms(),
        }
    )
    autonomy_store.save(blocked)
    task_manager.transition_task(
        task_id=project_run.task_id,
        to_state=TaskLifecycleState.PAUSED,
    )
    return blocked


def repository_task_plan_progress(
    checkpoint: ProjectCheckpoint,
    turn: ProjectTurnResult,
) -> tuple[bool, str | None]:
    plan, _, _ = project_checkpoints.updated_checkpoint_task_plan(checkpoint, turn)
    required = project_checkpoints.repository_task_plan_required(checkpoint)
    lifecycle = cast(
        Mapping[str, object],
        checkpoint.payload.get(REPOSITORY_LIFECYCLE_PAYLOAD_KEY, {}),
    )
    objective = cast(
        Mapping[str, object],
        lifecycle.get(checkpoint.project_run.objective_ledger_ref, {}),
    )
    criterion_ids = set(
        cast(tuple[str, ...] | list[str], objective.get("criterion_ids", ()))
    )
    incomplete = bool(
        required
        and not (
            plan
            and criterion_ids <= set(plan.criterion_ids)
            and plan.status == "completed"
            and all(step.status == "completed" for step in plan.steps)
        )
    )
    if not required or plan is None:
        return incomplete, checkpoint.project_run.current_milestone
    milestone = next(
        (
            step.description
            for step in plan.steps
            if step.status in {"pending", "in_progress"}
        ),
        plan.objective,
    )
    return incomplete, milestone


def classify_autonomy_loop_condition(
    *,
    condition: AutonomyLoopConditionKind,
    evidence_refs: tuple[str, ...] = (),
) -> AutonomyLoopJudgment:
    if condition == AutonomyLoopConditionKind.PRODUCTIVE:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.RUNNING,
            reason_code="progress_observed",
            evidence_refs=evidence_refs,
        )
    if condition == AutonomyLoopConditionKind.WAITING:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.WAITING_FOR_INPUT,
            requires_operator=True,
            reason_code="waiting_on_external_condition",
            evidence_refs=evidence_refs,
            next_resume_action="answer-input-request",
        )
    if condition == AutonomyLoopConditionKind.RETRYABLE_FAILURE:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.RUNNING,
            bounded_retry_allowed=True,
            reason_code="retryable_failure",
            evidence_refs=evidence_refs,
        )
    if condition == AutonomyLoopConditionKind.MISSING_CAPABILITY:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.BLOCKED,
            requires_operator=True,
            reason_code="missing_capability",
            evidence_refs=evidence_refs,
            next_resume_action="approve-or-install-capability",
        )
    if condition == AutonomyLoopConditionKind.DENIED:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.BLOCKED,
            requires_operator=True,
            reason_code="permission_denied",
            evidence_refs=evidence_refs,
            next_resume_action="revise-scope-or-approve",
        )
    if condition == AutonomyLoopConditionKind.DUPLICATE_ACTION:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.BLOCKED,
            requires_model_replan=True,
            reason_code="duplicate_action_bounded",
            evidence_refs=evidence_refs,
        )
    if condition == AutonomyLoopConditionKind.BUDGET_EXHAUSTED:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.BLOCKED,
            requires_operator=True,
            reason_code="budget_exhausted",
            evidence_refs=evidence_refs,
            next_resume_action="extend-budget-or-stop",
        )
    if condition == AutonomyLoopConditionKind.DEADLINE_EXHAUSTED:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.BLOCKED,
            requires_operator=True,
            reason_code="deadline_exhausted",
            evidence_refs=evidence_refs,
            next_resume_action="extend-deadline-or-stop",
        )
    if condition == AutonomyLoopConditionKind.STRATEGY_FAILURE:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.RUNNING,
            requires_model_replan=True,
            reason_code="strategy_failure_replan_required",
            evidence_refs=evidence_refs,
        )
    if condition == AutonomyLoopConditionKind.CANCELLED:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.CANCELLED,
            terminal=True,
            reason_code="cancelled",
            evidence_refs=evidence_refs,
        )
    return AutonomyLoopJudgment(
        condition=condition,
        run_status=AutonomyRunStatus.FAILED,
        terminal=True,
        reason_code="terminal_inability",
        evidence_refs=evidence_refs,
    )


def cycle_disposition(
    cycle_limit: int,
    *,
    cycle_number: int,
    condition: AutonomyLoopConditionKind,
    has_error: bool,
    condition_evidence_refs: tuple[str, ...],
    closure_status: ProjectDomainVerificationStatus,
    previous_replans: int,
    has_new_progress: bool,
    verification_waived: bool,
    task_plan_incomplete: bool,
) -> project_checkpoints.ProjectCycleDisposition:
    plan_disposition = project_checkpoints.task_plan_incomplete_disposition(
        cycle_limit,
        cycle_number,
        closure_status,
        has_error,
        task_plan_incomplete,
        previous_replans,
    )
    if plan_disposition is not None:
        return plan_disposition
    if closure_status == ProjectDomainVerificationStatus.VERIFIED and not has_error:
        return _productive_disposition(
            cycle_limit,
            cycle_number=cycle_number,
            closure_status=closure_status,
            previous_replans=previous_replans,
            has_new_progress=has_new_progress,
            verification_waived=verification_waived,
        )
    judgment = classify_autonomy_loop_condition(
        condition=condition,
        evidence_refs=condition_evidence_refs,
    )
    if condition != AutonomyLoopConditionKind.PRODUCTIVE:
        return _nonproductive_disposition(
            cycle_limit,
            cycle_number=cycle_number,
            judgment=judgment,
            previous_replans=previous_replans,
        )
    return _productive_disposition(
        cycle_limit,
        cycle_number=cycle_number,
        closure_status=closure_status,
        previous_replans=previous_replans,
        has_new_progress=has_new_progress,
        verification_waived=verification_waived,
    )


def _nonproductive_disposition(
    cycle_limit: int,
    *,
    cycle_number: int,
    judgment: AutonomyLoopJudgment,
    previous_replans: int,
) -> project_checkpoints.ProjectCycleDisposition:
    if judgment.requires_operator:
        decision = (
            ProjectCycleDecision.NEEDS_INPUT
            if judgment.run_status == AutonomyRunStatus.WAITING_FOR_INPUT
            else ProjectCycleDecision.BLOCKED
        )
        return (
            decision,
            judgment.run_status,
            AutonomyRunPhase.RECOVER,
            ProjectVerificationState.BLOCKED,
            previous_replans,
            judgment.reason_code,
        )
    if judgment.terminal:
        return (
            ProjectCycleDecision.BLOCKED,
            judgment.run_status,
            AutonomyRunPhase.CLOSED,
            ProjectVerificationState.FAILED,
            previous_replans,
            judgment.reason_code,
        )
    if cycle_number < cycle_limit and (
        judgment.bounded_retry_allowed
        or (judgment.requires_model_replan and previous_replans < 1)
    ):
        return (
            ProjectCycleDecision.CONTINUE,
            AutonomyRunStatus.RUNNING,
            AutonomyRunPhase.RECOVER,
            ProjectVerificationState.IN_PROGRESS,
            previous_replans + int(judgment.requires_model_replan),
            judgment.reason_code,
        )
    return (
        ProjectCycleDecision.BLOCKED,
        AutonomyRunStatus.BLOCKED,
        AutonomyRunPhase.CLOSED,
        ProjectVerificationState.BLOCKED,
        previous_replans,
        judgment.reason_code,
    )


def _productive_disposition(
    cycle_limit: int,
    *,
    cycle_number: int,
    closure_status: ProjectDomainVerificationStatus,
    previous_replans: int,
    has_new_progress: bool,
    verification_waived: bool,
) -> project_checkpoints.ProjectCycleDisposition:
    if closure_status == ProjectDomainVerificationStatus.VERIFIED:
        verification_state = (
            ProjectVerificationState.WAIVED
            if verification_waived
            else ProjectVerificationState.VERIFIED
        )
        return (
            ProjectCycleDecision.STOP,
            AutonomyRunStatus.COMPLETED,
            AutonomyRunPhase.CLOSED,
            verification_state,
            previous_replans,
            "verified",
        )
    if closure_status == ProjectDomainVerificationStatus.NEEDS_USER:
        return (
            ProjectCycleDecision.NEEDS_INPUT,
            AutonomyRunStatus.WAITING_FOR_INPUT,
            AutonomyRunPhase.RECOVER,
            ProjectVerificationState.BLOCKED,
            previous_replans,
            "needs_user",
        )
    if has_new_progress and cycle_number < cycle_limit:
        return (
            ProjectCycleDecision.CONTINUE,
            AutonomyRunStatus.RUNNING,
            AutonomyRunPhase.RECOVER,
            ProjectVerificationState.IN_PROGRESS,
            0,
            "verification_progress",
        )
    if previous_replans < 1 and cycle_number < cycle_limit:
        return (
            ProjectCycleDecision.CONTINUE,
            AutonomyRunStatus.RUNNING,
            AutonomyRunPhase.RECOVER,
            ProjectVerificationState.IN_PROGRESS,
            previous_replans + 1,
            "verification_replan",
        )
    failed = closure_status == ProjectDomainVerificationStatus.FAILED
    return (
        ProjectCycleDecision.BLOCKED,
        AutonomyRunStatus.BLOCKED,
        AutonomyRunPhase.CLOSED,
        ProjectVerificationState.FAILED if failed else ProjectVerificationState.BLOCKED,
        previous_replans,
        "verification_failed" if failed else "verification_blocked",
    )


def observe_repository_checks(
    run: AutonomyRun,
    checkpoint: ProjectCheckpoint,
    fetch_checks: Callable[[Mapping[str, object]], Mapping[str, object]] | None,
    *,
    task_manager: TaskManager,
    autonomy_store: AutonomyRunStore,
    owner_id: str,
    claim_ttl_seconds: int,
    triggering_cron_job_id: str | None,
    task_state: TaskLifecycleState,
) -> tuple[
    ProjectCheckpoint,
    dict[str, object] | None,
    tuple[AutonomyRun, ProjectCheckpoint] | None,
]:
    request = project_checkpoints.repository_check_request(checkpoint)
    if request is None:
        return checkpoint, None, None
    duration_limit = run.continuation_policy.max_wall_clock_ms
    if duration_limit is not None and now_ms() - run.created_at_ms >= duration_limit:
        return (
            checkpoint,
            project_checkpoints.repository_check_event(checkpoint, outcome="expired"),
            None,
        )
    if fetch_checks is None:
        raise RuntimeError("project check continuation requires a check reader")
    checkpoint = project_checkpoints.record_repository_check_result(
        checkpoint, fetch_checks(request)
    )
    event = project_checkpoints.repository_check_event(checkpoint)
    waiting = None
    if event["overall_result"] == "pending":
        waiting = _persist_repository_check_wait(
            task_manager=task_manager,
            autonomy_store=autonomy_store,
            run=run,
            checkpoint=checkpoint,
            owner_id=owner_id,
            claim_ttl_seconds=claim_ttl_seconds,
            triggering_cron_job_id=triggering_cron_job_id,
            task_state=task_state,
        )
    return checkpoint, event, waiting


def begin_next_repository_check(
    checkpoint: ProjectCheckpoint,
    *,
    observed_checkpoint: ProjectCheckpoint | None,
    enabled: bool,
) -> tuple[ProjectCheckpoint, dict[str, object] | None]:
    if observed_checkpoint is not None:
        checkpoint = project_checkpoints.carry_repository_check_observation(
            checkpoint, observed_checkpoint
        )
    if not enabled:
        return checkpoint, None
    checkpoint, started = project_checkpoints.begin_repository_check_observation(
        checkpoint
    )
    return (
        checkpoint,
        project_checkpoints.repository_check_event(checkpoint) if started else None,
    )


def finish_repository_check(
    run: AutonomyRun,
    checkpoint: ProjectCheckpoint,
    *,
    task_manager: TaskManager,
    autonomy_store: AutonomyRunStore,
    owner_id: str,
    claim_ttl_seconds: int,
    triggering_cron_job_id: str | None,
    outcome: str,
) -> tuple[AutonomyRun, ProjectCheckpoint]:
    checkpoint = project_checkpoints.record_repository_check_terminal(
        checkpoint,
        outcome=outcome,
    )
    project_run = checkpoint.project_run
    observation = cast(
        dict[str, object],
        project_checkpoints.repository_check_observation(checkpoint),
    )
    cancelled = outcome == "cancelled"
    task_state = (
        TaskLifecycleState.CANCELLED if cancelled else TaskLifecycleState.PAUSED
    )
    checkpoint_id = (
        f"{project_run.project_run_id}:checks:{observation['head_sha']}:{outcome}"
    )
    updated_project = project_run.model_copy(
        update={
            "status": (
                AutonomyRunStatus.CANCELLED if cancelled else AutonomyRunStatus.BLOCKED
            ),
            "phase": AutonomyRunPhase.CLOSED,
            "updated_at_ms": now_ms(),
            "last_checkpoint_id": checkpoint_id,
            "blocked_reason": None if cancelled else "check_wait_expired",
            "verification_state": ProjectVerificationState.BLOCKED,
            "task_state": task_state,
            "triggering_cron_job_id": triggering_cron_job_id,
            "next_wake_job_id": None,
        }
    )
    claim = task_manager.lifecycle_repository.acquire_project_cycle_claim(
        task_id=project_run.task_id,
        owner_id=owner_id,
        expected_checkpoint_id=checkpoint.checkpoint_id,
        ttl_seconds=claim_ttl_seconds,
    )
    try:
        committed = project_checkpoints.commit_project_run_checkpoint(
            task_manager,
            updated_project,
            claim=claim,
            checkpoint_id=checkpoint_id,
            triggering_cron_job_id=triggering_cron_job_id,
            next_wake_job_id=None,
            payload={
                **checkpoint.payload,
                "decision": (
                    ProjectCycleDecision.STOP.value
                    if cancelled
                    else ProjectCycleDecision.BLOCKED.value
                ),
                "decision_reason": f"repository_checks_{outcome}",
            },
        )
    finally:
        task_manager.lifecycle_repository.release_project_cycle_claim(claim)
    updated_run = run.model_copy(
        update={
            "checkpoint_id": committed.checkpoint_id,
            "status": updated_project.status,
            "phase": AutonomyRunPhase.CLOSED,
            "operator_summary": (
                "Project cancelled while waiting for checks."
                if cancelled
                else "Project check wait expired."
            ),
            "next_action_hint": (
                None if cancelled else "Resume with an explicitly extended time budget."
            ),
            "updated_at_ms": committed.project_run.updated_at_ms,
        }
    )
    autonomy_store.save(updated_run)
    if not cancelled:
        task_manager.transition_task(
            task_id=project_run.task_id,
            to_state=TaskLifecycleState.PAUSED,
        )
    return updated_run, committed


def repository_check_data(result: Mapping[str, object]) -> Mapping[str, object]:
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "GitHub check read failed"))
    return cast(Mapping[str, object], result["data"])


def _persist_repository_check_wait(
    *,
    task_manager: TaskManager,
    autonomy_store: AutonomyRunStore,
    run: AutonomyRun,
    checkpoint: ProjectCheckpoint,
    owner_id: str,
    claim_ttl_seconds: int,
    triggering_cron_job_id: str | None,
    task_state: TaskLifecycleState,
) -> tuple[AutonomyRun, ProjectCheckpoint]:
    committed = project_checkpoints.commit_repository_check_wait(
        task_manager,
        checkpoint,
        owner_id=owner_id,
        claim_ttl_seconds=claim_ttl_seconds,
        triggering_cron_job_id=triggering_cron_job_id,
        task_state=task_state,
    )
    updated_run = run.model_copy(
        update={
            "checkpoint_id": committed.checkpoint_id,
            "status": AutonomyRunStatus.RUNNING,
            "phase": AutonomyRunPhase.VALIDATE,
            "updated_at_ms": committed.project_run.updated_at_ms,
        }
    )
    autonomy_store.save(updated_run)
    return updated_run, committed


__all__ = [
    "AutonomyLoopConditionKind",
    "AutonomyLoopJudgment",
    "begin_next_repository_check",
    "block_unconfigured_project_domain",
    "checkpoint_decision",
    "cycle_disposition",
    "classify_autonomy_loop_condition",
    "finish_repository_check",
    "observe_repository_checks",
    "reconcile_run_projection",
    "recover_terminal_checkpoint",
    "record_project_cycle_interruption",
    "record_project_cycle_interruption_if_current",
    "repository_check_data",
    "terminal_checkpoint_projection",
    "transition_project_task",
]
