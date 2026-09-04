from __future__ import annotations

import json
from pathlib import Path

import pytest

from openminion.modules.task import (
    AutonomyRunPhase,
    AutonomyRunStatus,
    AutonomyRunStore,
    ProjectCycleDecision,
    TaskLifecycleState,
    TaskManager,
    TestEvidence as _TestEvidence,
    TestEvidenceStatus as _TestEvidenceStatus,
    build_autonomy_run,
    build_project_run_projection,
    load_latest_project_checkpoint,
    save_project_run_checkpoint,
)
from openminion.modules.task.project import AutonomyLoopConditionKind
from openminion.modules.task.project.reports import (
    build_project_report_from_task,
    render_project_report,
)
from openminion.base.errors import ErrorInfo
from openminion.modules.task.autonomy import now_ms
from openminion.modules.task.plan import TaskPlan, TaskPlanRevision
from openminion.modules.task.project.checkpoints import plan_checkpoint_payload
from openminion.services.runtime.project_worker import (
    ProjectTurnRequest,
    ProjectTurnResult,
    ProjectWorker,
    project_cycle_claim_ttl_seconds,
)


def _evidence(status: _TestEvidenceStatus) -> _TestEvidence:
    timestamp = now_ms()
    passed = status == _TestEvidenceStatus.PASSED
    return _TestEvidence(
        command=("verify",),
        cwd_ref="/workspace",
        started_at_ms=timestamp,
        ended_at_ms=timestamp,
        exit_code=0 if passed else 1,
        passed=1 if passed else 0,
        failed=0 if passed else 1,
        status=status,
        summary="verification passed" if passed else "verification failed",
    )


def _project(tmp_path, *, max_iterations: int = 3):
    store = AutonomyRunStore(root=tmp_path / "autonomy")
    run = build_autonomy_run(
        goal_text="Finish the fixture",
        goal_id="goal-1",
        session_id="session-1",
        workspace_ref=f"local:{tmp_path}#commit=abc;dirty=clean",
        max_iterations=max_iterations,
        agent_id="agent-1",
        verification_domain="coding",
        verification_commands=("verify",),
    ).model_copy(
        update={
            "task_id": "task-1",
            "status": AutonomyRunStatus.RUNNING,
            "phase": AutonomyRunPhase.EXECUTE,
        }
    )
    store.create(run)
    manager = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    manager.create_task(
        session_id=run.session_id,
        mode_name="project",
        goal=run.goal_text,
        agent_id="agent-1",
        task_id=run.task_id,
    )
    project_run = build_project_run_projection(
        run,
        objective_ledger_ref="project:objective",
        evidence_ledger_ref="project:evidence",
        resume_packet_ref="project:resume",
        operator_decision_log_ref="project:operator",
        capability_plan_ref="project:capabilities",
        metrics_summary_ref="project:metrics",
    )
    save_project_run_checkpoint(
        manager,
        project_run,
        checkpoint_id="initial",
        payload={"decision": "continue", "replan_count": 0},
    )
    return store, manager, run


def test_project_cycle_claim_covers_turn_and_verification_windows() -> None:
    run = build_autonomy_run(
        goal_text="Finish the fixture",
        goal_id="goal-1",
        session_id="session-1",
        workspace_ref="local:/workspace",
        max_iterations=1,
        verification_commands=("verify-a", "verify-b"),
        turn_timeout_seconds=1_800,
        verification_timeout_seconds=900,
    )

    assert project_cycle_claim_ttl_seconds(run) == 3_600


def test_project_worker_replans_once_then_commits_verified_completion(
    tmp_path,
) -> None:
    store, manager, run = _project(tmp_path)
    turns: list[ProjectTurnRequest] = []
    verification = iter(
        (
            (
                _evidence(_TestEvidenceStatus.FAILED),
                _evidence(_TestEvidenceStatus.PASSED),
            ),
            (_evidence(_TestEvidenceStatus.PASSED),),
        )
    )
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda request: (
            turns.append(request)
            or ProjectTurnResult(
                summary="worked",
                gateway_run_id=f"gateway:{request.cycle_id}",
                evidence_refs=(f"artifact:{request.cycle_id}",),
                evidence_kinds=("artifact",),
            )
        ),
        verify=lambda: next(verification),
        owner_id="worker-1",
    )

    result = worker.run(run.run_id, max_cycles=3)
    checkpoint = load_latest_project_checkpoint(manager, task_id="task-1")

    assert result.decision == ProjectCycleDecision.STOP
    assert result.run.status == AutonomyRunStatus.COMPLETED
    assert result.project_run.committed_cycle_count == 2
    assert len(turns) == 2
    assert "Prior verifier refs:" in turns[1].prompt
    assert "Prior verifier outcome:\nverification failed" in turns[1].prompt
    assert "Prior verifier outcome:\nverification passed" not in turns[1].prompt
    assert checkpoint is not None
    assert checkpoint.payload["gateway_run_id"].endswith(":cycle:2")
    assert checkpoint.expected_checkpoint_id.endswith(":cycle:1")
    assert manager.get_task("task-1").state == TaskLifecycleState.DONE
    report = build_project_report_from_task(manager, task_id="task-1")
    assert report.cycle_summaries == ("worked", "worked")
    assert "cycle_summaries:\n  1: worked\n  2: worked" in render_project_report(report)
    proof = json.loads(Path(result.run.proof_packet_ref or "").read_text())
    assert proof["cycle_summaries"] == ["worked", "worked"]


def test_project_worker_persists_verifier_linked_plan_revision_across_restart(
    tmp_path,
) -> None:
    store, manager, run = _project(tmp_path)
    prompts: list[str] = []
    plan = TaskPlan(
        plan_id="plan-1",
        objective="Ship the fixture",
        criterion_ids=["criterion-tests"],
        steps=[{"step_id": "build", "description": "Build it"}],
    )
    first = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda request: (
            prompts.append(request.prompt)
            or ProjectTurnResult(
                summary="planned",
                evidence_refs=("artifact:plan",),
                task_plan=plan,
            )
        ),
        verify=lambda: (_evidence(_TestEvidenceStatus.FAILED),),
        owner_id="worker-1",
    ).run_cycle(run.run_id)
    assert first.decision == ProjectCycleDecision.CONTINUE

    revision = TaskPlanRevision(
        plan_id="plan-1",
        revision_id="revision-1",
        criterion_ids=["criterion-tests"],
        verifier_refs=("verify:failed-1",),
        revised_steps=[{"step_id": "build", "description": "Repair it"}],
    )
    result = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda request: (
            prompts.append(request.prompt)
            or ProjectTurnResult(
                summary="repaired",
                evidence_refs=("artifact:repair",),
                task_plan_revision=revision,
            )
        ),
        verify=lambda: (_evidence(_TestEvidenceStatus.PASSED),),
        owner_id="worker-2",
    ).run_cycle(run.run_id)
    checkpoint = load_latest_project_checkpoint(manager, task_id="task-1")

    assert result.decision == ProjectCycleDecision.STOP
    assert checkpoint is not None
    assert checkpoint.payload["replan_count"] == 0
    assert checkpoint.payload["plan_revision_count"] == 1
    assert checkpoint.payload["task_plan"]["criterion_ids"] == ["criterion-tests"]
    assert checkpoint.payload["task_plan_revision"]["revision_id"] == "revision-1"
    assert "first action must use the existing plan loop-control tool" in prompts[0]
    assert "action=revise for plan_id=plan-1" in prompts[1]
    assert "verification:prun_" in prompts[1]
    assert "Prior verifier outcome:\nverification failed" in prompts[1]
    assert (
        build_project_report_from_task(
            manager,
            task_id="task-1",
        ).metrics.plan_revision_count
        == 1
    )


@pytest.mark.parametrize(
    "revision",
    (
        TaskPlanRevision(
            plan_id="other-plan",
            revision_id="revision-2",
            verifier_refs=["verify:failed-2"],
            revised_steps=[{"step_id": "build", "description": "Repair"}],
        ),
        TaskPlanRevision(
            plan_id="plan-1",
            revision_id="revision-1",
            verifier_refs=[],
            revised_steps=[{"step_id": "build", "description": "Repair"}],
        ),
        TaskPlanRevision(
            plan_id="plan-1",
            revision_id="revision-1",
            verifier_refs=[""],
            revised_steps=[{"step_id": "build", "description": "Repair"}],
        ),
        TaskPlanRevision(
            plan_id="plan-1",
            revision_id="revision-1",
            predecessor_revision_id="stale",
            verifier_refs=["verify:failed-2"],
            revised_steps=[{"step_id": "build", "description": "Repair"}],
        ),
        TaskPlanRevision(
            plan_id="plan-1",
            revision_id="revision-1",
            criterion_ids=["changed"],
            verifier_refs=["verify:failed-2"],
            revised_steps=[{"step_id": "build", "description": "Repair"}],
        ),
    ),
)
def test_project_worker_rejects_invalid_first_plan_revision(
    tmp_path,
    revision: TaskPlanRevision,
) -> None:
    store, manager, run = _project(tmp_path)
    checkpoint = load_latest_project_checkpoint(manager, task_id="task-1")
    assert checkpoint is not None
    checkpoint.payload["task_plan"] = TaskPlan(
        plan_id="plan-1",
        objective="Ship",
        criterion_ids=["criterion-tests"],
        steps=[{"step_id": "build", "description": "Build"}],
    ).model_dump(mode="json")

    with pytest.raises(ValueError):
        plan_checkpoint_payload(
            checkpoint,
            ProjectTurnResult(summary="bad revision", task_plan_revision=revision),
        )


@pytest.mark.parametrize(
    ("revision_id", "predecessor_revision_id"),
    (("revision-1", "revision-1"), ("revision-2", "stale")),
)
def test_project_worker_rejects_duplicate_or_stale_later_revision(
    tmp_path,
    revision_id: str,
    predecessor_revision_id: str,
) -> None:
    _store, manager, _run = _project(tmp_path)
    checkpoint = load_latest_project_checkpoint(manager, task_id="task-1")
    assert checkpoint is not None
    checkpoint.payload.update(
        {
            "task_plan": TaskPlan(
                plan_id="plan-1",
                objective="Ship",
                criterion_ids=["criterion-tests"],
                steps=[{"step_id": "build", "description": "Build"}],
            ).model_dump(mode="json"),
            "task_plan_revision": TaskPlanRevision(
                plan_id="plan-1",
                revision_id="revision-1",
                criterion_ids=["criterion-tests"],
                verifier_refs=["verify:failed-1"],
                revised_steps=[{"step_id": "build", "description": "Repair"}],
            ).model_dump(mode="json"),
            "plan_revision_count": 1,
        }
    )
    incoming = TaskPlanRevision(
        plan_id="plan-1",
        revision_id=revision_id,
        predecessor_revision_id=predecessor_revision_id,
        verifier_refs=["verify:failed-2"],
        revised_steps=[{"step_id": "build", "description": "Repair again"}],
    )

    with pytest.raises(ValueError):
        plan_checkpoint_payload(
            checkpoint,
            ProjectTurnResult(summary="bad revision", task_plan_revision=incoming),
        )


def test_project_worker_blocks_after_one_failed_replan(tmp_path) -> None:
    store, manager, run = _project(tmp_path, max_iterations=5)
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda request: ProjectTurnResult(summary=request.milestone),
        verify=lambda: (_evidence(_TestEvidenceStatus.FAILED),),
        owner_id="worker-1",
    )

    result = worker.run(run.run_id, max_cycles=5)

    assert result.decision == ProjectCycleDecision.BLOCKED
    assert result.project_run.committed_cycle_count == 2
    assert result.run.status == AutonomyRunStatus.BLOCKED
    assert manager.get_task("task-1").state == TaskLifecycleState.PAUSED


def test_project_worker_keeps_resumed_run_active_during_next_turn(tmp_path) -> None:
    store, manager, run = _project(tmp_path, max_iterations=1)
    first = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda request: ProjectTurnResult(summary=request.milestone),
        verify=lambda: (_evidence(_TestEvidenceStatus.FAILED),),
        owner_id="worker-1",
    ).run(run.run_id, max_cycles=1)
    assert first.run.status == AutonomyRunStatus.BLOCKED

    store.save(
        first.run.model_copy(
            update={
                "continuation_policy": first.run.continuation_policy.model_copy(
                    update={"max_iterations": 2}
                )
            }
        )
    )
    resumed = store.transition(
        run.run_id,
        status=AutonomyRunStatus.RUNNING,
        phase=AutonomyRunPhase.EXECUTE,
    )
    manager.transition_task(
        task_id="task-1",
        to_state=TaskLifecycleState.ACTIVE,
    )

    def interrupted_turn(_request: ProjectTurnRequest) -> ProjectTurnResult:
        assert store.require(resumed.run_id).status == AutonomyRunStatus.RUNNING
        raise RuntimeError("provider interrupted")

    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=interrupted_turn,
        verify=lambda: (),
        owner_id="worker-2",
    )

    with pytest.raises(RuntimeError, match="provider interrupted"):
        worker.run(resumed.run_id, max_cycles=1)

    assert store.require(resumed.run_id).status == AutonomyRunStatus.RUNNING


def test_project_error_dominates_passing_verifier(tmp_path) -> None:
    store, manager, run = _project(tmp_path)
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda _request: ProjectTurnResult(
            summary="provider timed out",
            condition=AutonomyLoopConditionKind.RETRYABLE_FAILURE,
            error=ErrorInfo(
                code="provider_timeout",
                message="provider timed out",
                details={"request_id": "req-1"},
            ),
        ),
        verify=lambda: (_evidence(_TestEvidenceStatus.PASSED),),
        owner_id="worker-1",
    )

    result = worker.run(run.run_id, max_cycles=3)
    checkpoint = load_latest_project_checkpoint(manager, task_id="task-1")

    assert result.decision == ProjectCycleDecision.BLOCKED
    assert result.run.status == AutonomyRunStatus.BLOCKED
    assert checkpoint is not None
    assert checkpoint.payload["error"] == {
        "code": "provider_timeout",
        "message": "provider timed out",
        "details": {"request_id": "req-1"},
    }


def test_project_worker_continues_while_typed_progress_is_new(tmp_path) -> None:
    store, manager, run = _project(tmp_path, max_iterations=5)
    turns = 0

    def turn(request: ProjectTurnRequest) -> ProjectTurnResult:
        nonlocal turns
        turns += 1
        return ProjectTurnResult(
            summary=f"completed milestone {turns}",
            evidence_refs=(f"artifact:{request.cycle_id}",),
        )

    verification = iter(
        (
            (_evidence(_TestEvidenceStatus.FAILED),),
            (_evidence(_TestEvidenceStatus.FAILED),),
            (_evidence(_TestEvidenceStatus.FAILED),),
            (_evidence(_TestEvidenceStatus.PASSED),),
        )
    )
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=turn,
        verify=lambda: next(verification),
        owner_id="worker-1",
    )

    result = worker.run(run.run_id, max_cycles=5)

    assert result.decision == ProjectCycleDecision.STOP
    assert result.project_run.committed_cycle_count == 4
    assert result.run.status == AutonomyRunStatus.COMPLETED
    assert turns == 4


def test_project_worker_blocks_when_progress_ref_repeats(tmp_path) -> None:
    store, manager, run = _project(tmp_path, max_iterations=5)
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda _request: ProjectTurnResult(
            summary="same claimed progress",
            evidence_refs=("artifact:unchanged",),
        ),
        verify=lambda: (_evidence(_TestEvidenceStatus.FAILED),),
        owner_id="worker-1",
    )

    result = worker.run(run.run_id, max_cycles=5)

    assert result.decision == ProjectCycleDecision.BLOCKED
    assert result.project_run.committed_cycle_count == 3
    assert result.project_run.progress_refs == ("artifact:unchanged",)


def test_project_worker_reconciles_duplicate_cron_delivery_without_turn(
    tmp_path,
) -> None:
    store, manager, run = _project(tmp_path)
    turn_count = 0

    def turn(_request: ProjectTurnRequest) -> ProjectTurnResult:
        nonlocal turn_count
        turn_count += 1
        return ProjectTurnResult(summary="not verified")

    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=turn,
        verify=lambda: (_evidence(_TestEvidenceStatus.FAILED),),
        owner_id="worker-1",
    )
    first = worker.run_cycle(run.run_id, triggering_cron_job_id="cron-1")
    replay = worker.run_cycle(run.run_id, triggering_cron_job_id="cron-1")

    assert first.decision == ProjectCycleDecision.CONTINUE
    assert replay.reconciled_only is True
    assert replay.decision == ProjectCycleDecision.CONTINUE
    assert turn_count == 1


def test_project_worker_service_has_no_api_or_cli_import() -> None:
    source = Path("src/openminion/services/runtime/project_worker.py").read_text(
        encoding="utf-8"
    )

    assert "openminion.api" not in source
    assert "openminion.cli" not in source


@pytest.mark.parametrize(
    ("condition", "expected_decision", "expected_status"),
    (
        (
            AutonomyLoopConditionKind.WAITING,
            ProjectCycleDecision.NEEDS_INPUT,
            AutonomyRunStatus.WAITING_FOR_INPUT,
        ),
        (
            AutonomyLoopConditionKind.MISSING_CAPABILITY,
            ProjectCycleDecision.BLOCKED,
            AutonomyRunStatus.BLOCKED,
        ),
        (
            AutonomyLoopConditionKind.DENIED,
            ProjectCycleDecision.BLOCKED,
            AutonomyRunStatus.BLOCKED,
        ),
        (
            AutonomyLoopConditionKind.TERMINAL_INABILITY,
            ProjectCycleDecision.BLOCKED,
            AutonomyRunStatus.FAILED,
        ),
    ),
)
def test_project_worker_uses_typed_terminal_conditions(
    tmp_path,
    condition,
    expected_decision,
    expected_status,
) -> None:
    store, manager, run = _project(tmp_path)
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda _request: ProjectTurnResult(
            summary="typed condition",
            condition=condition,
            evidence_refs=(f"condition:{condition.value}",),
        ),
        verify=lambda: (),
        owner_id="worker-1",
    )

    result = worker.run(run.run_id, max_cycles=3)

    assert result.decision == expected_decision
    assert result.run.status == expected_status
    assert result.project_run.committed_cycle_count == 1


def test_retryable_failure_gets_one_bounded_retry_before_verified_completion(
    tmp_path,
) -> None:
    store, manager, run = _project(tmp_path)
    conditions = iter(
        (
            AutonomyLoopConditionKind.RETRYABLE_FAILURE,
            AutonomyLoopConditionKind.PRODUCTIVE,
        )
    )
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda _request: ProjectTurnResult(
            summary="retry",
            condition=next(conditions),
        ),
        verify=iter(
            (
                (_evidence(_TestEvidenceStatus.FAILED),),
                (_evidence(_TestEvidenceStatus.PASSED),),
            )
        ).__next__,
        owner_id="worker-1",
    )

    result = worker.run(run.run_id, max_cycles=3)

    assert result.decision == ProjectCycleDecision.STOP
    assert result.project_run.committed_cycle_count == 2


def test_verified_workspace_closes_even_if_turn_is_waiting_for_an_extra_action(
    tmp_path,
) -> None:
    store, manager, run = _project(tmp_path)
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda _request: ProjectTurnResult(
            summary="requested an unnecessary extra command",
            condition=AutonomyLoopConditionKind.WAITING,
        ),
        verify=lambda: (_evidence(_TestEvidenceStatus.PASSED),),
        owner_id="worker-1",
    )

    result = worker.run(run.run_id, max_cycles=3)

    assert result.decision == ProjectCycleDecision.STOP
    assert result.run.status == AutonomyRunStatus.COMPLETED
    assert result.run.operator_summary == "Configured project verification passed."
    assert manager.get_task("task-1").state == TaskLifecycleState.DONE


def test_duplicate_effect_replans_once_without_duplicating_effect_refs(
    tmp_path,
) -> None:
    store, manager, run = _project(tmp_path)
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda _request: ProjectTurnResult(
            summary="duplicate effect",
            condition=AutonomyLoopConditionKind.DUPLICATE_ACTION,
            evidence_refs=("effect:1",),
            effect_refs=("effect:1",),
        ),
        verify=lambda: (),
        owner_id="worker-1",
    )

    result = worker.run(run.run_id, max_cycles=3)

    assert result.decision == ProjectCycleDecision.BLOCKED
    assert result.project_run.committed_cycle_count == 2
    assert result.project_run.effect_refs == ("effect:1",)


def test_prose_only_progress_cannot_close_without_verifier(tmp_path) -> None:
    store, manager, run = _project(tmp_path, max_iterations=2)
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda _request: ProjectTurnResult(summary="Everything is complete."),
        verify=lambda: (),
        owner_id="worker-1",
    )

    result = worker.run(run.run_id, max_cycles=2)

    assert result.decision == ProjectCycleDecision.BLOCKED
    assert result.run.status == AutonomyRunStatus.BLOCKED


def test_cancelled_task_stops_without_another_turn(tmp_path) -> None:
    store, manager, run = _project(tmp_path)
    manager.transition_task(task_id="task-1", to_state=TaskLifecycleState.CANCELLED)
    turn_count = 0

    def turn(_request: ProjectTurnRequest) -> ProjectTurnResult:
        nonlocal turn_count
        turn_count += 1
        return ProjectTurnResult(summary="unexpected")

    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=turn,
        verify=lambda: (),
        owner_id="worker-1",
    )

    result = worker.run(run.run_id, max_cycles=1)

    assert result.run.status == AutonomyRunStatus.CANCELLED
    assert turn_count == 0
