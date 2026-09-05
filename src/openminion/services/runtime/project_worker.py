"""Run bounded project cycles through existing task and verification owners."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from openminion.base.logging import format_structured_event, get_logger
from openminion.modules.config import resolve_module_data_root, resolve_module_home_root
from openminion.modules.task import (
    AutonomyRun,
    AutonomyRunPhase,
    AutonomyRunStatus,
    AutonomyRunStore,
    ProjectCycleDecision,
    ProjectCheckpoint,
    ProjectRun,
    ProjectVerificationState,
    TaskLifecycleRecord,
    TaskLifecycleRepository,
    TaskLifecycleState,
    TaskManager,
    TestEvidence,
    TestEvidenceStatus,
    build_terminal_proof_packet,
    load_latest_project_checkpoint,
)
from openminion.modules.task.plan import TaskPlan
from openminion.modules.task.autonomy import VerificationWaiver, now_ms
from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
from openminion.modules.task.project import (
    AutonomyLoopConditionKind,
    AutonomyLoopJudgment,
    ProjectDomainVerificationContract,
    ProjectDomainVerificationEvidence,
    ProjectDomainVerificationStatus,
    ProjectTurnRequest,
    ProjectTurnResult,
    ProjectVerificationDomain,
    ProjectVerificationClosure,
    classify_autonomy_loop_condition,
    commit_project_run_checkpoint,
    evaluate_project_verification_closure,
    project_condition_from_metadata,
    project_metadata_refs,
    project_runtime_payload,
    project_turn_from_payload,
    project_turn_inbound_metadata,
    project_workspace,
    run_project_verification_commands,
)
from openminion.modules.task.project import checkpoints as project_cp

_LOGGER = get_logger("project_worker")


@dataclass(frozen=True)
class ProjectWorkerResult:
    run: AutonomyRun
    project_run: ProjectRun
    decision: ProjectCycleDecision
    verification: tuple[TestEvidence, ...]
    reconciled_only: bool = False


def project_cycle_claim_ttl_seconds(run: AutonomyRun) -> int:
    selectors = run.execution_selectors
    verifier_timeout = int(selectors.verification_timeout_seconds)
    verifier_window = verifier_timeout * len(selectors.verification_commands)
    return int(selectors.turn_timeout_seconds) + verifier_window


def build_cron_project_worker(
    *,
    runtime: Any,
    cron_store: Any,
    run_id: str,
    payload: Mapping[str, object],
    execute: Callable[[dict[str, object]], Mapping[str, object]],
    owner_id: str,
) -> tuple[ProjectWorker, TaskManager]:
    autonomy_store = AutonomyRunStore()
    autonomy_run = autonomy_store.require(run_id)
    runtime_env = getattr(runtime.config.runtime, "env", None)
    raw_home_root = str(getattr(runtime, "home_root", "") or "").strip()
    home_root = resolve_module_home_root(
        Path(raw_home_root) if raw_home_root else None,
        runtime_env,
        fallback_to_cwd=True,
    )
    data_root = resolve_module_data_root(home_root=home_root, env=runtime_env)
    assert data_root is not None
    task_manager = TaskManager(
        cron_repository=cron_store,
        lifecycle_repository=TaskLifecycleRepository(
            db_path=(data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve()
        ),
    )
    workspace = project_workspace(autonomy_run.workspace_ref)
    project_payload = project_runtime_payload(
        payload,
        permission_profile_id=autonomy_run.permission_profile_id,
        workspace=workspace,
        turn_timeout_seconds=autonomy_run.execution_selectors.turn_timeout_seconds,
    )
    worker = ProjectWorker(
        task_manager=task_manager,
        autonomy_store=autonomy_store,
        turn=lambda request: project_turn_from_payload(
            request,
            payload=project_payload,
            execute=execute,
        ),
        verify=lambda: run_project_verification_commands(
            autonomy_run.execution_selectors.verification_commands,
            workspace=workspace,
            timeout_seconds=autonomy_run.execution_selectors.verification_timeout_seconds,
        ),
        claim_ttl_seconds=project_cycle_claim_ttl_seconds(autonomy_run),
        owner_id=owner_id,
    )
    return worker, task_manager


@dataclass(frozen=True)
class _CycleEvaluation:
    cycle_id: str
    turn: ProjectTurnResult
    verification: tuple[TestEvidence, ...]
    closure_status: ProjectDomainVerificationStatus
    closure_payload: dict[str, object]
    task_plan: TaskPlan | None
    task_plan_required: bool
    decision: ProjectCycleDecision
    status: AutonomyRunStatus
    phase: AutonomyRunPhase
    verification_state: ProjectVerificationState
    replan_count: int
    reason: str


class ProjectWorker:
    def __init__(
        self,
        *,
        task_manager: TaskManager,
        autonomy_store: AutonomyRunStore,
        turn: Callable[[ProjectTurnRequest], ProjectTurnResult],
        verify: Callable[[], tuple[TestEvidence, ...]],
        claim_ttl_seconds: int = 120,
        owner_id: str | None = None,
    ) -> None:
        self._task_manager = task_manager
        self._autonomy_store = autonomy_store
        self._turn = turn
        self._verify = verify
        self._claim_ttl_seconds = max(1, int(claim_ttl_seconds))
        self._owner_id = owner_id or f"project-worker-{uuid4().hex}"

    def run(
        self,
        run_id: str,
        *,
        max_cycles: int,
        triggering_cron_job_id: str | None = None,
    ) -> ProjectWorkerResult:
        if max_cycles < 1:
            raise ValueError("max_cycles must be greater than zero")
        result: ProjectWorkerResult
        for _ in range(max_cycles):
            result = self.run_cycle(
                run_id,
                triggering_cron_job_id=triggering_cron_job_id,
            )
            if result.decision != ProjectCycleDecision.CONTINUE:
                return result
            triggering_cron_job_id = None
        return result

    def run_cycle(
        self,
        run_id: str,
        *,
        triggering_cron_job_id: str | None = None,
    ) -> ProjectWorkerResult:
        run, checkpoint = self._load_cycle(run_id)
        reconciled = self._reconciled_cron_cycle(
            run,
            checkpoint,
            triggering_cron_job_id=triggering_cron_job_id,
        )
        if reconciled is not None:
            return reconciled
        task = self._task_manager.get_task(run.task_id or "")
        if task is None:
            raise KeyError(f"task not found: {run.task_id}")
        inactive = self._inactive_task_result(run, checkpoint, task)
        if inactive is not None:
            return inactive
        cycle_number = checkpoint.project_run.committed_cycle_count + 1
        if cycle_number > run.continuation_policy.max_iterations:
            return self._budget_blocked(run, checkpoint.project_run)
        claim = self._task_manager.lifecycle_repository.acquire_project_cycle_claim(
            task_id=task.task_id,
            owner_id=self._owner_id,
            expected_checkpoint_id=checkpoint.checkpoint_id,
            ttl_seconds=self._claim_ttl_seconds,
        )
        try:
            evaluation = self._evaluate_cycle(
                run,
                checkpoint,
                cycle_number=cycle_number,
            )
            updated_project = self._updated_project_run(
                run,
                checkpoint,
                task,
                evaluation,
                triggering_cron_job_id=triggering_cron_job_id,
            )
            turn_error = evaluation.turn.error
            plan_payload = project_cp.plan_checkpoint_payload(
                checkpoint,
                evaluation.turn,
            )
            committed = commit_project_run_checkpoint(
                self._task_manager,
                updated_project,
                claim=claim,
                checkpoint_id=evaluation.cycle_id,
                triggering_cron_job_id=triggering_cron_job_id,
                next_wake_job_id=updated_project.next_wake_job_id,
                payload={
                    "decision": evaluation.decision.value,
                    "summary": evaluation.turn.summary,
                    "gateway_run_id": evaluation.turn.gateway_run_id,
                    "verification": [
                        item.model_dump(mode="json") for item in evaluation.verification
                    ],
                    "verification_closure": evaluation.closure_payload,
                    "condition": evaluation.turn.condition.value,
                    "decision_reason": evaluation.reason,
                    "replan_count": evaluation.replan_count,
                    **plan_payload,
                    **project_cp.advance_repository_lifecycle_payload(
                        checkpoint,
                        updated_project,
                        turn=evaluation.turn,
                        verification_count=len(evaluation.verification),
                        next_action=evaluation.decision.value,
                    ),
                    **({"error": turn_error.to_dict()} if turn_error else {}),
                },
            )
            return self._finalize_cycle(
                run,
                updated_project,
                evaluation,
                committed=committed,
            )
        finally:
            self._task_manager.lifecycle_repository.release_project_cycle_claim(claim)

    def _load_cycle(self, run_id: str) -> tuple[AutonomyRun, ProjectCheckpoint]:
        run = self._autonomy_store.require(run_id)
        if not run.task_id:
            raise ValueError("autonomy run is missing task_id")
        checkpoint = load_latest_project_checkpoint(
            self._task_manager,
            task_id=run.task_id,
        )
        if checkpoint is None:
            raise ValueError("project worker requires an initial checkpoint")
        return self._reconcile_run_projection(run, checkpoint.project_run), checkpoint

    def _reconciled_cron_cycle(
        self,
        run: AutonomyRun,
        checkpoint: ProjectCheckpoint,
        *,
        triggering_cron_job_id: str | None,
    ) -> ProjectWorkerResult | None:
        if not triggering_cron_job_id:
            return None
        if checkpoint.triggering_cron_job_id != triggering_cron_job_id:
            return None
        return ProjectWorkerResult(
            run=run,
            project_run=checkpoint.project_run,
            decision=self._checkpoint_decision(checkpoint.payload),
            verification=(),
            reconciled_only=True,
        )

    def _inactive_task_result(
        self,
        run: AutonomyRun,
        checkpoint: ProjectCheckpoint,
        task: TaskLifecycleRecord,
    ) -> ProjectWorkerResult | None:
        if task.state == TaskLifecycleState.PAUSED:
            return ProjectWorkerResult(
                run=run,
                project_run=checkpoint.project_run,
                decision=ProjectCycleDecision.NEEDS_INPUT,
                verification=(),
            )
        terminal_status = {
            TaskLifecycleState.CANCELLED: AutonomyRunStatus.CANCELLED,
            TaskLifecycleState.DONE: AutonomyRunStatus.COMPLETED,
            TaskLifecycleState.FAILED: AutonomyRunStatus.FAILED,
        }.get(task.state)
        if terminal_status is None:
            return None
        terminal_run = run.model_copy(
            update={
                "status": terminal_status,
                "phase": AutonomyRunPhase.CLOSED,
                "updated_at_ms": now_ms(),
            }
        )
        self._autonomy_store.save(terminal_run)
        return ProjectWorkerResult(
            run=terminal_run,
            project_run=checkpoint.project_run,
            decision=(
                ProjectCycleDecision.BLOCKED
                if task.state == TaskLifecycleState.FAILED
                else ProjectCycleDecision.STOP
            ),
            verification=(),
        )

    def _evaluate_cycle(
        self,
        run: AutonomyRun,
        checkpoint: ProjectCheckpoint,
        *,
        cycle_number: int,
    ) -> _CycleEvaluation:
        project_run = checkpoint.project_run
        cycle_id = f"{project_run.project_run_id}:cycle:{cycle_number}"
        milestone = project_run.current_milestone or run.goal_text
        request = ProjectTurnRequest(
            run_id=run.run_id,
            project_run_id=project_run.project_run_id,
            task_id=project_run.task_id,
            goal_id=project_run.goal_id,
            session_id=run.session_id,
            cycle_id=cycle_id,
            milestone=milestone,
            prompt=self._cycle_prompt(run, project_run, checkpoint.payload, milestone),
        )
        self._log_cycle("project.cycle.started", run, project_run, cycle_id=cycle_id)
        turn_result = self._turn(request)
        verification = self._verify()
        closure = self._verification_closure(
            run,
            turn_result=turn_result,
            verification=verification,
        )
        raw_replan_count = checkpoint.payload.get("replan_count", 0)
        previous_replans = raw_replan_count if isinstance(raw_replan_count, int) else 0
        existing_progress_refs = {*project_run.progress_refs, *project_run.effect_refs}
        has_new_progress = any(
            ref not in existing_progress_refs
            for ref in (*turn_result.evidence_refs, *turn_result.effect_refs)
        )
        task_plan, _, _ = project_cp.updated_checkpoint_task_plan(
            checkpoint,
            turn_result,
        )
        task_plan_required = project_cp.repository_task_plan_required(checkpoint)
        disposition = self._cycle_disposition(
            run,
            cycle_number=cycle_number,
            condition=turn_result.condition,
            has_error=turn_result.error is not None,
            condition_evidence_refs=turn_result.evidence_refs,
            closure_status=closure.status,
            previous_replans=previous_replans,
            has_new_progress=has_new_progress,
            verification_waived=bool(
                run.execution_selectors.verification_waiver_reason
            ),
            task_plan_required=task_plan_required,
            task_plan_completed=bool(
                task_plan
                and task_plan.status == "completed"
                and all(step.status == "completed" for step in task_plan.steps)
            ),
        )
        return _CycleEvaluation(
            cycle_id,
            turn_result,
            verification,
            closure.status,
            closure.model_dump(mode="json"),
            task_plan,
            task_plan_required,
            *disposition,
        )

    def _updated_project_run(
        self,
        run: AutonomyRun,
        checkpoint: ProjectCheckpoint,
        task: TaskLifecycleRecord,
        evaluation: _CycleEvaluation,
        *,
        triggering_cron_job_id: str | None,
    ) -> ProjectRun:
        project_run = checkpoint.project_run
        next_wake_job_id = (
            f"{project_run.project_run_id}:wake:{project_run.committed_cycle_count + 1}"
            if evaluation.decision == ProjectCycleDecision.CONTINUE
            else None
        )
        verification = evaluation.verification
        validation_refs = tuple(
            f"verification:{evaluation.cycle_id}:{index}:{item.status.value}"
            for index, item in enumerate(verification, start=1)
        )
        latest_verification_ms = max(
            (item.ended_at_ms for item in verification), default=0
        )
        return project_run.model_copy(
            update={
                "status": evaluation.status,
                "phase": evaluation.phase,
                "updated_at_ms": max(
                    project_run.updated_at_ms, latest_verification_ms, now_ms()
                ),
                "last_checkpoint_id": evaluation.cycle_id,
                "blocked_reason": (
                    None
                    if evaluation.decision
                    in {ProjectCycleDecision.CONTINUE, ProjectCycleDecision.STOP}
                    else evaluation.reason
                ),
                "verification_state": evaluation.verification_state,
                "task_state": task.state,
                "committed_cycle_count": project_run.committed_cycle_count + 1,
                "cycle_limit": run.continuation_policy.max_iterations,
                "progress_refs": tuple(
                    dict.fromkeys(
                        (*project_run.progress_refs, *evaluation.turn.evidence_refs)
                    )
                ),
                "effect_refs": tuple(
                    dict.fromkeys(
                        (*project_run.effect_refs, *evaluation.turn.effect_refs)
                    )
                ),
                "verifier_refs": tuple(
                    dict.fromkeys((*project_run.verifier_refs, *validation_refs))
                ),
                "triggering_cron_job_id": triggering_cron_job_id,
                "next_wake_job_id": next_wake_job_id,
                "current_milestone": self._next_project_milestone(
                    project_run.current_milestone,
                    evaluation,
                ),
            }
        )

    @staticmethod
    def _next_project_milestone(
        current_milestone: str | None,
        evaluation: _CycleEvaluation,
    ) -> str | None:
        if not evaluation.task_plan_required or evaluation.task_plan is None:
            return current_milestone
        for step in evaluation.task_plan.steps:
            if step.status in {"pending", "in_progress"}:
                return step.description
        return evaluation.task_plan.objective

    def _finalize_cycle(
        self,
        run: AutonomyRun,
        project_run: ProjectRun,
        evaluation: _CycleEvaluation,
        *,
        committed: ProjectCheckpoint,
    ) -> ProjectWorkerResult:
        operator_summary = evaluation.turn.summary
        if (
            evaluation.status == AutonomyRunStatus.COMPLETED
            and evaluation.turn.condition != AutonomyLoopConditionKind.PRODUCTIVE
        ):
            operator_summary = "Configured project verification passed."
        updated_run = run.model_copy(
            update={
                "checkpoint_id": committed.checkpoint_id,
                "status": evaluation.status,
                "phase": evaluation.phase,
                "operator_summary": operator_summary,
                "next_action_hint": (
                    "Resume the project after resolving the verifier blocker."
                    if evaluation.decision == ProjectCycleDecision.BLOCKED
                    else None
                ),
                "updated_at_ms": project_run.updated_at_ms,
                "completed_at_ms": (
                    project_run.updated_at_ms
                    if evaluation.status == AutonomyRunStatus.COMPLETED
                    else None
                ),
            }
        )
        self._autonomy_store.save(updated_run)
        self._transition_task(
            project_run.task_id,
            decision=evaluation.decision,
            status=evaluation.status,
        )
        if evaluation.decision != ProjectCycleDecision.CONTINUE:
            self._write_terminal_proof(
                updated_run,
                verification=evaluation.verification,
            )
            updated_run = self._autonomy_store.require(updated_run.run_id)
        self._log_cycle(
            "project.cycle.finished",
            run,
            project_run,
            cycle_id=evaluation.cycle_id,
            decision=evaluation.decision.value,
            verification=evaluation.closure_status.value,
        )
        return ProjectWorkerResult(
            run=updated_run,
            project_run=project_run,
            decision=evaluation.decision,
            verification=evaluation.verification,
        )

    @staticmethod
    def _log_cycle(
        event: str,
        run: AutonomyRun,
        project_run: ProjectRun,
        *,
        cycle_id: str,
        **facts: object,
    ) -> None:
        _LOGGER.info(
            format_structured_event(
                event,
                run_id=run.run_id,
                task_id=project_run.task_id,
                project_run_id=project_run.project_run_id,
                cycle_id=cycle_id,
                **facts,
            )
        )

    def _reconcile_run_projection(
        self,
        run: AutonomyRun,
        project_run: ProjectRun,
    ) -> AutonomyRun:
        checkpoint_matches = run.checkpoint_id == project_run.last_checkpoint_id
        if checkpoint_matches and run.status == project_run.status:
            return run
        resumed = run.status == AutonomyRunStatus.RUNNING
        reconciled = run.model_copy(
            update={
                "checkpoint_id": project_run.last_checkpoint_id,
                "status": run.status if resumed else project_run.status,
                "phase": run.phase if resumed else project_run.phase,
                "updated_at_ms": project_run.updated_at_ms,
            }
        )
        self._autonomy_store.save(reconciled)
        return reconciled

    def _verification_closure(
        self,
        run: AutonomyRun,
        *,
        turn_result: ProjectTurnResult,
        verification: tuple[TestEvidence, ...],
    ) -> ProjectVerificationClosure:
        passed = bool(verification) and all(
            item.status == TestEvidenceStatus.PASSED for item in verification
        )
        evidence_kinds = tuple(turn_result.evidence_kinds)
        waiver_reason = str(
            run.execution_selectors.verification_waiver_reason or ""
        ).strip()
        if waiver_reason:
            evidence_kinds = (*evidence_kinds, "waiver")
            verification_refs: tuple[str, ...] = (f"waiver:{run.run_id}",)
        else:
            verification_refs = ()
        if passed and "verification" not in evidence_kinds:
            evidence_kinds = (*evidence_kinds, "verification")
        verification_refs += tuple(
            f"command:{index}:{item.status.value}"
            for index, item in enumerate(verification, start=1)
        )
        selectors = run.execution_selectors
        return evaluate_project_verification_closure(
            ProjectDomainVerificationContract(
                domain=ProjectVerificationDomain(selectors.verification_domain),
                required_evidence_kinds=selectors.required_evidence_kinds,
                verifier_ref=selectors.verifier_ref,
            ),
            ProjectDomainVerificationEvidence(
                domain=ProjectVerificationDomain(selectors.verification_domain),
                evidence_kinds=evidence_kinds,
                evidence_refs=tuple(
                    dict.fromkeys((*turn_result.evidence_refs, *verification_refs))
                ),
                verifier_failed=(
                    not waiver_reason
                    and any(
                        item.status == TestEvidenceStatus.FAILED
                        for item in verification
                    )
                ),
            ),
        )

    def _write_terminal_proof(
        self,
        run: AutonomyRun,
        *,
        verification: tuple[TestEvidence, ...],
    ) -> None:
        waiver_reason = str(
            run.execution_selectors.verification_waiver_reason or ""
        ).strip()
        waiver = (
            VerificationWaiver(reason=waiver_reason, recorded_at_ms=run.updated_at_ms)
            if waiver_reason
            else None
        )
        if waiver is not None:
            validation_summary = "Project verification was explicitly waived."
        elif verification and all(
            item.status == TestEvidenceStatus.PASSED for item in verification
        ):
            validation_summary = "Project verification commands passed."
        else:
            validation_summary = "Project closed without verified completion."
        packet = build_terminal_proof_packet(
            run,
            validation_summary=validation_summary,
            final_operator_summary=run.operator_summary or "Autonomy project closed.",
            cycle_summaries=project_cp.project_cycle_summaries(
                self._task_manager, task_id=run.task_id or ""
            ),
            tests_run=verification,
            verification_waiver=waiver,
        )
        self._autonomy_store.write_proof_packet(packet)

    @staticmethod
    def _cycle_disposition(
        run: AutonomyRun,
        *,
        cycle_number: int,
        condition: AutonomyLoopConditionKind,
        has_error: bool,
        condition_evidence_refs: tuple[str, ...],
        closure_status: ProjectDomainVerificationStatus,
        previous_replans: int,
        has_new_progress: bool,
        verification_waived: bool,
        task_plan_required: bool,
        task_plan_completed: bool,
    ) -> tuple[
        ProjectCycleDecision,
        AutonomyRunStatus,
        AutonomyRunPhase,
        ProjectVerificationState,
        int,
        str,
    ]:
        if (
            closure_status == ProjectDomainVerificationStatus.VERIFIED
            and not has_error
            and task_plan_required
            and not task_plan_completed
        ):
            if cycle_number < run.continuation_policy.max_iterations:
                return (
                    ProjectCycleDecision.CONTINUE,
                    AutonomyRunStatus.RUNNING,
                    AutonomyRunPhase.EXECUTE,
                    ProjectVerificationState.IN_PROGRESS,
                    previous_replans,
                    "task_plan_incomplete",
                )
            return (
                ProjectCycleDecision.BLOCKED,
                AutonomyRunStatus.BLOCKED,
                AutonomyRunPhase.CLOSED,
                ProjectVerificationState.BLOCKED,
                previous_replans,
                "task_plan_incomplete",
            )
        if closure_status == ProjectDomainVerificationStatus.VERIFIED and not has_error:
            return ProjectWorker._productive_disposition(
                run,
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
            return ProjectWorker._nonproductive_disposition(
                run,
                cycle_number=cycle_number,
                judgment=judgment,
                previous_replans=previous_replans,
            )
        return ProjectWorker._productive_disposition(
            run,
            cycle_number=cycle_number,
            closure_status=closure_status,
            previous_replans=previous_replans,
            has_new_progress=has_new_progress,
            verification_waived=verification_waived,
        )

    @staticmethod
    def _nonproductive_disposition(
        run: AutonomyRun,
        *,
        cycle_number: int,
        judgment: AutonomyLoopJudgment,
        previous_replans: int,
    ) -> tuple[
        ProjectCycleDecision,
        AutonomyRunStatus,
        AutonomyRunPhase,
        ProjectVerificationState,
        int,
        str,
    ]:
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
        if cycle_number < run.continuation_policy.max_iterations and (
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

    @staticmethod
    def _productive_disposition(
        run: AutonomyRun,
        *,
        cycle_number: int,
        closure_status: ProjectDomainVerificationStatus,
        previous_replans: int,
        has_new_progress: bool,
        verification_waived: bool,
    ) -> tuple[
        ProjectCycleDecision,
        AutonomyRunStatus,
        AutonomyRunPhase,
        ProjectVerificationState,
        int,
        str,
    ]:
        if closure_status == ProjectDomainVerificationStatus.VERIFIED:
            return (
                ProjectCycleDecision.STOP,
                AutonomyRunStatus.COMPLETED,
                AutonomyRunPhase.CLOSED,
                (
                    ProjectVerificationState.WAIVED
                    if verification_waived
                    else ProjectVerificationState.VERIFIED
                ),
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
        if has_new_progress and cycle_number < run.continuation_policy.max_iterations:
            return (
                ProjectCycleDecision.CONTINUE,
                AutonomyRunStatus.RUNNING,
                AutonomyRunPhase.RECOVER,
                ProjectVerificationState.IN_PROGRESS,
                0,
                "verification_progress",
            )
        if (
            previous_replans < 1
            and cycle_number < run.continuation_policy.max_iterations
        ):
            return (
                ProjectCycleDecision.CONTINUE,
                AutonomyRunStatus.RUNNING,
                AutonomyRunPhase.RECOVER,
                ProjectVerificationState.IN_PROGRESS,
                previous_replans + 1,
                "verification_replan",
            )
        return (
            ProjectCycleDecision.BLOCKED,
            AutonomyRunStatus.BLOCKED,
            AutonomyRunPhase.CLOSED,
            (
                ProjectVerificationState.FAILED
                if closure_status == ProjectDomainVerificationStatus.FAILED
                else ProjectVerificationState.BLOCKED
            ),
            previous_replans,
            (
                "verification_failed"
                if closure_status == ProjectDomainVerificationStatus.FAILED
                else "verification_blocked"
            ),
        )

    @staticmethod
    def _checkpoint_decision(payload: dict[str, object]) -> ProjectCycleDecision:
        return ProjectCycleDecision(str(payload.get("decision") or "blocked"))

    @staticmethod
    def _cycle_prompt(
        run: AutonomyRun,
        project_run: ProjectRun,
        checkpoint_payload: Mapping[str, Any],
        milestone: str,
    ) -> str:
        lines = [
            run.goal_text,
            "",
            f"Current milestone: {milestone}",
            f"Committed cycles: {project_run.committed_cycle_count}",
            "Work on the smallest useful next step. Inspect current state before editing.",
            "Do not claim completion; the configured verifier owns completion.",
        ]
        active_plan = checkpoint_payload.get("task_plan")
        if not isinstance(active_plan, Mapping):
            lines.append(
                "Your first action must use the existing plan loop-control tool "
                "to declare a durable task plan with "
                "continue_plan_autonomously=true, then continue with its first step."
            )
        if project_run.verifier_refs:
            lines.append(
                "Prior verifier refs: " + ", ".join(project_run.verifier_refs[-5:])
            )
        if verification := checkpoint_payload.get("verification"):
            failed = [
                item
                for item in verification
                if item["status"] == TestEvidenceStatus.FAILED.value
            ]
            outcome = (failed or verification)[-1]
            lines.extend(("Prior verifier outcome:", outcome["summary"]))
            if failed and isinstance(active_plan, Mapping):
                plan_id = str(active_plan.get("plan_id") or "").strip()
                verifier_refs = ", ".join(project_run.verifier_refs[-5:])
                lines.append(
                    "Your first action must use the existing plan loop-control "
                    f"tool with action=revise for plan_id={plan_id}. Use a new "
                    "revision_id, set continue_plan_autonomously=true, and bind "
                    f"verifier_refs to: {verifier_refs}."
                )
        if project_run.progress_refs:
            lines.append(
                "Prior progress refs: " + ", ".join(project_run.progress_refs[-5:])
            )
        return "\n".join(lines)

    def _budget_blocked(
        self,
        run: AutonomyRun,
        project_run: ProjectRun,
    ) -> ProjectWorkerResult:
        blocked = run.model_copy(
            update={
                "status": AutonomyRunStatus.BLOCKED,
                "phase": AutonomyRunPhase.CLOSED,
                "operator_summary": "Project cycle budget exhausted.",
                "next_action_hint": "Resume with an explicitly extended cycle budget.",
            }
        )
        self._autonomy_store.save(blocked)
        return ProjectWorkerResult(
            run=blocked,
            project_run=project_run,
            decision=ProjectCycleDecision.BLOCKED,
            verification=(),
        )

    def _transition_task(
        self,
        task_id: str,
        *,
        decision: ProjectCycleDecision,
        status: AutonomyRunStatus,
    ) -> None:
        target = None
        if status == AutonomyRunStatus.FAILED:
            target = TaskLifecycleState.FAILED
        elif decision == ProjectCycleDecision.STOP:
            target = TaskLifecycleState.DONE
        elif decision in {
            ProjectCycleDecision.BLOCKED,
            ProjectCycleDecision.NEEDS_INPUT,
        }:
            target = TaskLifecycleState.PAUSED
        if target is not None:
            self._task_manager.transition_task(task_id=task_id, to_state=target)


__all__ = [
    "ProjectTurnRequest",
    "ProjectTurnResult",
    "ProjectWorker",
    "ProjectWorkerResult",
    "build_cron_project_worker",
    "project_cycle_claim_ttl_seconds",
    "project_condition_from_metadata",
    "project_metadata_refs",
    "project_turn_inbound_metadata",
    "project_turn_from_payload",
]
