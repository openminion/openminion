from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openminion.modules.task.autonomy import (
    AutonomyRun,
    AutonomyRunPhase,
    AutonomyRunStatus,
    AutonomyRunStore,
    now_ms,
)
from openminion.modules.task.runtime.lifecycle import TaskLifecycleState, TaskManager

from . import checkpoints as project_checkpoints
from .models import ProjectCheckpoint, ProjectCycleDecision, ProjectVerificationState


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
                AutonomyRunStatus.CANCELLED
                if cancelled
                else AutonomyRunStatus.BLOCKED
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
                None
                if cancelled
                else "Resume with an explicitly extended time budget."
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
    "classify_autonomy_loop_condition",
    "finish_repository_check",
    "observe_repository_checks",
    "repository_check_data",
]
