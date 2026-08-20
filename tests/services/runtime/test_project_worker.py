from __future__ import annotations

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
from openminion.modules.task.autonomy import now_ms
from openminion.services.runtime.project_worker import (
    ProjectTurnRequest,
    ProjectTurnResult,
    ProjectWorker,
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


def test_project_worker_replans_once_then_commits_verified_completion(
    tmp_path,
) -> None:
    store, manager, run = _project(tmp_path)
    turns: list[ProjectTurnRequest] = []
    verification = iter(
        (
            (_evidence(_TestEvidenceStatus.FAILED),),
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
    assert checkpoint is not None
    assert checkpoint.expected_checkpoint_id.endswith(":cycle:1")
    assert manager.get_task("task-1").state == TaskLifecycleState.DONE


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
        verify=lambda: (_evidence(_TestEvidenceStatus.PASSED),),
        owner_id="worker-1",
    )

    result = worker.run(run.run_id, max_cycles=3)

    assert result.decision == ProjectCycleDecision.STOP
    assert result.project_run.committed_cycle_count == 2


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
