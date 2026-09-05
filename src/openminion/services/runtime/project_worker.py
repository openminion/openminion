"""Run bounded project cycles through existing task and verification owners."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from openminion.base.logging import format_structured_event, get_logger
from openminion.modules.brain.loop.strategies.coding.contracts import (
    select_coding_allowed_tools,
)
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
    load_latest_project_checkpoint,
)
from openminion.modules.task.autonomy import now_ms
from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
from openminion.modules.task.project import (
    AutonomyLoopConditionKind,
    AutonomyLoopJudgment,
    ProjectDomainVerificationStatus,
    ProjectTurnRequest,
    ProjectTurnResult,
    build_project_terminal_proof,
    classify_autonomy_loop_condition,
    commit_project_run_checkpoint,
    evaluate_project_turn_verification,
    project_condition_from_metadata,
    project_cycle_checkpoint_payload,
    project_cycle_prompt,
    project_metadata_refs,
    project_runtime_payload,
    project_turn_from_payload,
    project_turn_inbound_metadata,
    project_workspace,
    run_project_verification_commands,
)
from openminion.modules.task.project import (
    checkpoints as project_cp,
    effects as project_effects,
)
from openminion.modules.task.project import policy as project_policy
from openminion.modules.task.project import progress as project_progress
from openminion.services.runtime.routine_context import ToolRegistryPreTurnContext
from openminion.tools.github.interfaces import TOOL_GITHUB_FETCH_CHECKS

_LOGGER = get_logger("project_worker")

@dataclass(frozen=True)
class ProjectWorkerResult:
    run: AutonomyRun
    project_run: ProjectRun
    decision: ProjectCycleDecision
    verification: tuple[TestEvidence, ...]
    reconciled_only: bool = False
    check_events: tuple[dict[str, object], ...] = ()


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
    check_context = ToolRegistryPreTurnContext(
        registry=runtime.tools,
        routine_id=autonomy_run.run_id,
        session_id=autonomy_run.session_id,
        agent_id=autonomy_run.execution_selectors.agent_id,
    )

    def fetch_checks(args: Mapping[str, object]) -> Mapping[str, object]:
        result = check_context.invoke_tool(name=TOOL_GITHUB_FETCH_CHECKS, args=args)
        data: Mapping[str, object] = project_progress.repository_check_data(result)
        return data

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
        fetch_checks=fetch_checks,
    )
    return worker, task_manager


@dataclass(frozen=True)
class _CycleEvaluation:
    cycle_id: str
    turn: ProjectTurnResult
    verification: tuple[TestEvidence, ...]
    closure_status: ProjectDomainVerificationStatus
    closure_payload: dict[str, object]
    next_milestone: str | None
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
        fetch_checks: Callable[[Mapping[str, object]], Mapping[str, object]]
        | None = None,
    ) -> None:
        self._task_manager = task_manager
        self._autonomy_store = autonomy_store
        self._turn = turn
        self._verify = verify
        self._claim_ttl_seconds = max(1, int(claim_ttl_seconds))
        self._owner_id = owner_id or f"project-worker-{uuid4().hex}"
        self._fetch_checks = fetch_checks

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
        inactive = self._inactive_task_result(
            run,
            checkpoint,
            task,
            triggering_cron_job_id=triggering_cron_job_id,
        )
        if inactive is not None:
            return inactive
        check_events: tuple[dict[str, object], ...] = ()
        checkpoint, check_event, waiting = project_progress.observe_repository_checks(
            run,
            checkpoint,
            self._fetch_checks,
            task_manager=self._task_manager,
            autonomy_store=self._autonomy_store,
            owner_id=self._owner_id,
            claim_ttl_seconds=self._claim_ttl_seconds,
            triggering_cron_job_id=triggering_cron_job_id,
            task_state=task.state,
        )
        observed_checkpoint = checkpoint if check_event is not None else None
        check_events = (check_event,) if check_event is not None else ()
        if check_event is not None:
            if check_event["overall_result"] == "expired":
                return self._terminal_repository_check_result(
                    run,
                    checkpoint,
                    outcome="expired",
                    triggering_cron_job_id=triggering_cron_job_id,
                )
            if waiting is not None:
                updated_run, committed = waiting
                return ProjectWorkerResult(
                    run=updated_run,
                    project_run=committed.project_run,
                    decision=ProjectCycleDecision.CONTINUE,
                    verification=(),
                    check_events=check_events,
                )
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
            return self._run_claimed_cycle(
                run=run,
                checkpoint=checkpoint,
                observed_checkpoint=observed_checkpoint,
                task=task,
                claim=claim,
                cycle_number=cycle_number,
                triggering_cron_job_id=triggering_cron_job_id,
                check_events=check_events,
            )
        finally:
            self._task_manager.lifecycle_repository.release_project_cycle_claim(claim)

    def _run_claimed_cycle(
        self,
        *,
        run: AutonomyRun,
        checkpoint: ProjectCheckpoint,
        observed_checkpoint: ProjectCheckpoint | None,
        task: TaskLifecycleRecord,
        claim: Any,
        cycle_number: int,
        triggering_cron_job_id: str | None,
        check_events: tuple[dict[str, object], ...],
    ) -> ProjectWorkerResult:
        evaluation = self._evaluate_cycle(run, checkpoint, cycle_number=cycle_number)
        checkpoint = cast(
            ProjectCheckpoint,
            load_latest_project_checkpoint(
                self._task_manager,
                task_id=task.task_id,
            ),
        )
        checkpoint, next_check_event = project_progress.begin_next_repository_check(
            checkpoint,
            observed_checkpoint=observed_checkpoint,
            enabled=self._fetch_checks is not None,
        )
        if next_check_event is not None:
            evaluation = replace(
                evaluation,
                decision=ProjectCycleDecision.CONTINUE,
                status=AutonomyRunStatus.RUNNING,
                phase=AutonomyRunPhase.VALIDATE,
                verification_state=ProjectVerificationState.IN_PROGRESS,
                reason="waiting_for_checks",
            )
            check_events = (*check_events, next_check_event)
        elif observed_checkpoint is not None and (
            project_cp.repository_check_facts(observed_checkpoint)["overall_result"]
            == "failure"
        ):
            evaluation = replace(
                evaluation,
                decision=ProjectCycleDecision.BLOCKED,
                status=AutonomyRunStatus.BLOCKED,
                phase=AutonomyRunPhase.RECOVER,
                verification_state=ProjectVerificationState.BLOCKED,
                reason="repository_checks_failed",
            )
        waiting_for_checks = next_check_event is not None
        updated_project = self._updated_project_run(
            run,
            checkpoint,
            task,
            evaluation,
            triggering_cron_job_id=triggering_cron_job_id,
        )
        committed = commit_project_run_checkpoint(
            self._task_manager,
            updated_project,
            claim=claim,
            checkpoint_id=evaluation.cycle_id,
            triggering_cron_job_id=triggering_cron_job_id,
            next_wake_job_id=updated_project.next_wake_job_id,
            payload=project_cycle_checkpoint_payload(
                evaluation.turn,
                effect_payload=project_effects.project_effect_checkpoint_payload(
                    checkpoint
                ),
                plan_payload=project_cp.plan_checkpoint_payload(
                    checkpoint, evaluation.turn
                ),
                repository_payload=project_cp.advance_repository_lifecycle_payload(
                    checkpoint,
                    updated_project,
                    turn=evaluation.turn,
                    verification_count=len(evaluation.verification),
                    next_action=evaluation.decision.value,
                ),
                decision=evaluation.decision,
                verification=evaluation.verification,
                verification_closure=evaluation.closure_payload,
                decision_reason=evaluation.reason,
                replan_count=evaluation.replan_count,
                waiting_for_checks=waiting_for_checks,
            ),
        )
        return self._finalize_cycle(
            run,
            updated_project,
            evaluation,
            committed=committed,
            check_events=check_events,
        )

    def _terminal_repository_check_result(
        self,
        run: AutonomyRun,
        checkpoint: ProjectCheckpoint,
        *,
        outcome: str,
        triggering_cron_job_id: str | None,
    ) -> ProjectWorkerResult:
        updated_run, committed = project_progress.finish_repository_check(
            run,
            checkpoint,
            task_manager=self._task_manager,
            autonomy_store=self._autonomy_store,
            owner_id=self._owner_id,
            claim_ttl_seconds=self._claim_ttl_seconds,
            triggering_cron_job_id=triggering_cron_job_id,
            outcome=outcome,
        )
        decision = (
            ProjectCycleDecision.STOP
            if outcome == "cancelled"
            else ProjectCycleDecision.BLOCKED
        )
        return ProjectWorkerResult(
            run=updated_run,
            project_run=committed.project_run,
            decision=decision,
            verification=(),
            check_events=(project_cp.repository_check_event(committed),),
        )

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
        expected_check_wake = (
            checkpoint.project_run.next_wake_job_id
            if project_cp.repository_check_request(checkpoint) is not None
            else None
        )
        stale_check_wake = (
            expected_check_wake is not None
            and expected_check_wake != triggering_cron_job_id
        )
        if not triggering_cron_job_id and not stale_check_wake:
            return None
        delivered = checkpoint.triggering_cron_job_id == triggering_cron_job_id
        if not delivered and not stale_check_wake:
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
        *,
        triggering_cron_job_id: str | None,
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
        if (
            task.state == TaskLifecycleState.CANCELLED
            and project_cp.repository_check_request(checkpoint) is not None
        ):
            return self._terminal_repository_check_result(
                run,
                checkpoint,
                outcome="cancelled",
                triggering_cron_job_id=triggering_cron_job_id,
            )
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
            prompt=project_cycle_prompt(
                run,
                checkpoint,
                milestone,
                repository_check_observation=(
                    project_cp.repository_check_observation(checkpoint)
                ),
            ),
            allowed_tools=tuple(
                sorted(
                    select_coding_allowed_tools(
                        project_launch_approved=(
                            project_policy.repository_project_launch_approved(
                                checkpoint
                            )
                        ),
                        release_approved=(
                            project_policy.repository_release_tools_approved(checkpoint)
                        ),
                    )
                )
            ),
        )
        self._log_cycle("project.cycle.started", run, project_run, cycle_id=cycle_id)
        turn_result = self._turn(request)
        verification = self._verify()
        closure = evaluate_project_turn_verification(run, turn_result, verification)
        raw_replan_count = checkpoint.payload.get("replan_count", 0)
        previous_replans = raw_replan_count if isinstance(raw_replan_count, int) else 0
        existing_progress_refs = {*project_run.progress_refs, *project_run.effect_refs}
        has_new_progress = any(
            ref not in existing_progress_refs
            for ref in (*turn_result.evidence_refs, *turn_result.effect_refs)
        )
        task_plan_incomplete, next_milestone = project_cp.repository_task_plan_progress(
            checkpoint,
            turn_result,
        )
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
            task_plan_incomplete=task_plan_incomplete,
        )
        return _CycleEvaluation(
            cycle_id,
            turn_result,
            verification,
            closure.status,
            closure.model_dump(mode="json"),
            next_milestone,
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
                "current_milestone": evaluation.next_milestone,
            }
        )

    def _finalize_cycle(
        self,
        run: AutonomyRun,
        project_run: ProjectRun,
        evaluation: _CycleEvaluation,
        *,
        committed: ProjectCheckpoint,
        check_events: tuple[dict[str, object], ...] = (),
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
            check_events=check_events,
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

    def _write_terminal_proof(
        self,
        run: AutonomyRun,
        *,
        verification: tuple[TestEvidence, ...],
    ) -> None:
        packet = build_project_terminal_proof(
            run,
            verification=verification,
            cycle_summaries=project_cp.project_cycle_summaries(
                self._task_manager, task_id=run.task_id or ""
            ),
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
        task_plan_incomplete: bool,
    ) -> project_cp.ProjectCycleDisposition:
        plan_disposition: project_cp.ProjectCycleDisposition | None = (
            project_cp.task_plan_incomplete_disposition(
                run,
                cycle_number,
                closure_status,
                has_error,
                task_plan_incomplete,
                previous_replans,
            )
        )
        if plan_disposition is not None:
            return plan_disposition
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
