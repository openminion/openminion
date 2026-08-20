"""Run bounded project cycles through existing task and verification owners."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import uuid4

from openminion.base.logging import format_structured_event, get_logger
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
    TaskLifecycleState,
    TaskManager,
    TestEvidence,
    TestEvidenceStatus,
    build_terminal_proof_packet,
    load_latest_project_checkpoint,
)
from openminion.modules.task.autonomy import VerificationWaiver, now_ms
from openminion.modules.task.project import (
    AutonomyLoopConditionKind,
    AutonomyLoopJudgment,
    ProjectDomainVerificationContract,
    ProjectDomainVerificationEvidence,
    ProjectDomainVerificationStatus,
    ProjectVerificationDomain,
    ProjectVerificationClosure,
    classify_autonomy_loop_condition,
    commit_project_run_checkpoint,
    evaluate_project_verification_closure,
)

_LOGGER = get_logger("project_worker")


def project_metadata_refs(
    metadata: Mapping[str, object],
    *keys: str,
) -> tuple[str, ...]:
    refs: list[str] = []
    for key in keys:
        values = metadata.get(key)
        if isinstance(values, (list, tuple)):
            refs.extend(str(value).strip() for value in values if str(value).strip())
    return tuple(dict.fromkeys(refs))


@dataclass(frozen=True)
class ProjectTurnRequest:
    run_id: str
    project_run_id: str
    task_id: str
    goal_id: str
    session_id: str
    cycle_id: str
    milestone: str
    prompt: str


@dataclass(frozen=True)
class ProjectTurnResult:
    summary: str
    condition: AutonomyLoopConditionKind = AutonomyLoopConditionKind.PRODUCTIVE
    evidence_refs: tuple[str, ...] = ()
    evidence_kinds: tuple[str, ...] = ()
    effect_refs: tuple[str, ...] = ()
    tool_call_count: int = 0


@dataclass(frozen=True)
class ProjectWorkerResult:
    run: AutonomyRun
    project_run: ProjectRun
    decision: ProjectCycleDecision
    verification: tuple[TestEvidence, ...]
    reconciled_only: bool = False


def project_turn_from_payload(
    request: ProjectTurnRequest,
    *,
    payload: Mapping[str, object],
    execute: Callable[[dict[str, object]], Mapping[str, object]],
) -> ProjectTurnResult:
    turn_payload = dict(payload)
    turn_payload.update(
        {
            "kind": "agentTurn",
            "message": request.prompt,
            "session_id": request.session_id,
            "goal_id": request.goal_id,
            "project_run_id": request.project_run_id,
            "task_id": request.task_id,
            "cycle_id": request.cycle_id,
        }
    )
    turn_result = execute(turn_payload)
    if bool(turn_result.get("error")):
        raise RuntimeError(str(turn_result.get("summary") or "project turn failed"))
    raw_metadata = turn_result.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    return ProjectTurnResult(
        summary=str(turn_result.get("summary") or "Project cycle completed."),
        condition=AutonomyLoopConditionKind(
            str(metadata.get("project_condition") or "productive")
        ),
        evidence_refs=project_metadata_refs(
            metadata,
            "evidence_refs",
            "artifact_refs",
        ),
        evidence_kinds=project_metadata_refs(metadata, "evidence_kinds"),
        effect_refs=project_metadata_refs(metadata, "effect_refs"),
        tool_call_count=(
            int(metadata["tool_call_count"])
            if isinstance(metadata.get("tool_call_count"), int)
            else 0
        ),
    )


@dataclass(frozen=True)
class _CycleEvaluation:
    cycle_id: str
    turn: ProjectTurnResult
    verification: tuple[TestEvidence, ...]
    closure_status: ProjectDomainVerificationStatus
    closure_payload: dict[str, object]
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
        result: ProjectWorkerResult | None = None
        for _ in range(max_cycles):
            result = self.run_cycle(
                run_id,
                triggering_cron_job_id=triggering_cron_job_id,
            )
            if result.decision != ProjectCycleDecision.CONTINUE:
                return result
            triggering_cron_job_id = None
        assert result is not None
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
                    "verification": [
                        item.model_dump(mode="json") for item in evaluation.verification
                    ],
                    "verification_closure": evaluation.closure_payload,
                    "condition": evaluation.turn.condition.value,
                    "decision_reason": evaluation.reason,
                    "replan_count": evaluation.replan_count,
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
            prompt=self._cycle_prompt(run, project_run, milestone=milestone),
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
        disposition = self._cycle_disposition(
            run,
            cycle_number=cycle_number,
            condition=turn_result.condition,
            condition_evidence_refs=turn_result.evidence_refs,
            closure_status=closure.status,
            previous_replans=previous_replans,
            verification_waived=bool(
                run.execution_selectors.verification_waiver_reason
            ),
        )
        return _CycleEvaluation(
            cycle_id,
            turn_result,
            verification,
            closure.status,
            closure.model_dump(mode="json"),
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
        validation_refs = tuple(
            f"verification:{evaluation.cycle_id}:{index}:{item.status.value}"
            for index, item in enumerate(evaluation.verification, start=1)
        )
        return project_run.model_copy(
            update={
                "status": evaluation.status,
                "phase": evaluation.phase,
                "updated_at_ms": max(
                    project_run.updated_at_ms,
                    max(
                        (item.ended_at_ms for item in evaluation.verification),
                        default=0,
                    ),
                    now_ms(),
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
            }
        )

    def _finalize_cycle(
        self,
        run: AutonomyRun,
        project_run: ProjectRun,
        evaluation: _CycleEvaluation,
        *,
        committed: ProjectCheckpoint,
    ) -> ProjectWorkerResult:
        updated_run = run.model_copy(
            update={
                "checkpoint_id": committed.checkpoint_id,
                "status": evaluation.status,
                "phase": evaluation.phase,
                "operator_summary": evaluation.turn.summary,
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
        if (
            run.checkpoint_id == project_run.last_checkpoint_id
            and run.status == project_run.status
        ):
            return run
        reconciled = run.model_copy(
            update={
                "checkpoint_id": project_run.last_checkpoint_id,
                "status": project_run.status,
                "phase": project_run.phase,
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
        verification_refs = (
            *verification_refs,
            *(
                f"command:{index}:{item.status.value}"
                for index, item in enumerate(verification, start=1)
            ),
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
        condition_evidence_refs: tuple[str, ...],
        closure_status: ProjectDomainVerificationStatus,
        previous_replans: int,
        verification_waived: bool,
    ) -> tuple[
        ProjectCycleDecision,
        AutonomyRunStatus,
        AutonomyRunPhase,
        ProjectVerificationState,
        int,
        str,
    ]:
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
        *,
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
        if project_run.verifier_refs:
            lines.append(
                "Prior verifier refs: " + ", ".join(project_run.verifier_refs[-5:])
            )
        if project_run.progress_refs:
            lines.append(
                "Verified progress refs: " + ", ".join(project_run.progress_refs[-5:])
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
    "project_metadata_refs",
    "project_turn_from_payload",
]
