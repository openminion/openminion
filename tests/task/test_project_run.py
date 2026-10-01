from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from openminion.modules.task import (
    AutonomyRunPhase,
    AutonomyRunStatus,
    DOMAIN_WORKFLOW_REF,
    GAP_ASSESSMENT_REF,
    ProjectBudgetPolicy,
    ProjectCapabilityArea,
    ProjectCapabilityDisposition,
    ProjectControlAction,
    ProjectCycleDecision,
    ProjectMetricSnapshot,
    ProjectObjectiveContract,
    ProjectObjectiveLedger,
    ProjectOutcomeClassification,
    ProjectPermissionDecision,
    ProjectVerificationState,
    TaskLifecycleRecord,
    TaskLifecycleState,
    TaskManager,
    apply_project_control,
    build_autonomy_run,
    build_project_capability_matrix,
    build_project_policy_state,
    build_project_report,
    build_project_run_projection,
    capability_rows_requiring_resolution,
    consume_project_permission_grant,
    evaluate_project_budget,
    evaluate_project_permission,
    find_open_project_worker,
    issue_project_permission_grant,
    load_latest_project_checkpoint,
    load_project_policy_state,
    record_project_cycle,
    render_project_capability_matrix,
    render_project_control_result,
    render_project_report,
    render_project_run_summary,
    replay_project_cycles,
    resume_project_run_from_latest_checkpoint,
    save_project_policy_state,
    save_project_run_checkpoint,
)
from openminion.modules.task.plan import (
    TaskPlan,
    TaskPlanRevision,
    TaskPlanStepBlocked,
    TaskPlanStepCompleted,
    TaskPlanTerminalSignal,
)
from openminion.modules.task.project import checkpoints as project_checkpoints
from openminion.modules.task.project.turn import ProjectTurnResult
from openminion.modules.task.project.turn import project_checkpoint_guidance


def _autonomy_run():
    run = build_autonomy_run(
        goal_text="Ship the project worker contract",
        goal_id="goal-1",
        session_id="session-1",
        workspace_ref="local:/workspace#commit=abc;dirty=clean",
        max_iterations=3,
    )
    return run.model_copy(
        update={
            "task_id": "task-1",
            "checkpoint_id": "checkpoint-1",
            "status": AutonomyRunStatus.RUNNING,
            "phase": AutonomyRunPhase.EXECUTE,
        }
    )


def _task_record(task_id: str = "task-1") -> TaskLifecycleRecord:
    return TaskLifecycleRecord(
        task_id=task_id,
        cron_job_id="cron-1",
        agent_id="agent-1",
        state=TaskLifecycleState.ACTIVE,
        created_at="2026-07-03T00:00:00Z",
        updated_at="2026-07-03T00:00:01Z",
        cancelled_at=None,
        completed_at=None,
        failed_at=None,
        failure_reason=None,
    )


def _create_project_task(tmp_path) -> tuple[TaskManager, object]:
    manager = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    manager.create_task(
        session_id="session-1",
        mode_name="project",
        goal="ship a project worker",
        agent_id="agent-1",
        task_id="task-1",
    )
    project_run = build_project_run_projection(
        _autonomy_run(),
        objective_ledger_ref="artifact:objective.json",
        evidence_ledger_ref="artifact:evidence.jsonl",
        resume_packet_ref="artifact:resume.json",
        operator_decision_log_ref="artifact:operator-decisions.jsonl",
        capability_plan_ref="artifact:capabilities.json",
        metrics_summary_ref="artifact:metrics.json",
    )
    save_project_run_checkpoint(manager, project_run, checkpoint_id="checkpoint-1")
    return manager, project_run


def test_operator_guidance_updates_allocate_distinct_revisions(tmp_path) -> None:
    manager, _ = _create_project_task(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(
                apply_project_control,
                manager,
                task_id="task-1",
                action=ProjectControlAction.REPRIORITIZE,
                priority="finish the failing test first",
            ),
            executor.submit(
                apply_project_control,
                manager,
                task_id="task-1",
                action=ProjectControlAction.ANSWER_INPUT,
                input_request_id="input-1",
                answer="use the existing migration",
            ),
        )
        for future in futures:
            future.result()

    record = manager.get_task("task-1")
    assert record is not None
    answer = record.metadata["operator_answers"][0]
    assert record.metadata["operator_guidance_revision"] == 2
    assert {record.metadata["priority_revision"], answer["revision"]} == {1, 2}
    manager.close()


def test_project_redirect_survives_restart_and_is_consumed_once(tmp_path) -> None:
    manager, _ = _create_project_task(tmp_path)
    apply_project_control(
        manager,
        task_id="task-1",
        action=ProjectControlAction.PAUSE,
    )
    result = apply_project_control(
        manager,
        task_id="task-1",
        action=ProjectControlAction.REDIRECT,
        direction="finish the release notes before packaging",
    )
    manager.close()

    restarted = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    record = restarted.get_task("task-1")
    checkpoint = load_latest_project_checkpoint(restarted, task_id="task-1")
    assert record is not None
    assert checkpoint is not None
    guidance, revision = project_checkpoint_guidance(record.metadata, checkpoint)

    assert result.direction == "finish the release notes before packaging"
    assert (
        "direction_queued_for_next_cycle: finish the release notes before packaging"
        in render_project_control_result(result)
    )
    assert guidance == {"direction": "finish the release notes before packaging"}
    assert revision == 1
    consumed = checkpoint.model_copy(
        update={
            "payload": {
                **checkpoint.payload,
                "operator_guidance_consumed_revision": revision,
            }
        }
    )
    assert project_checkpoint_guidance(record.metadata, consumed) == ({}, revision)
    restarted.close()


def test_project_redirect_waits_for_active_cycle_claim(tmp_path) -> None:
    manager, _ = _create_project_task(tmp_path)
    claim = manager.lifecycle_repository.acquire_project_cycle_claim(
        task_id="task-1",
        owner_id="worker-1",
        expected_checkpoint_id="checkpoint-1",
    )
    apply_project_control(
        manager,
        task_id="task-1",
        action=ProjectControlAction.PAUSE,
    )

    with pytest.raises(ValueError, match="active project cycle"):
        apply_project_control(
            manager,
            task_id="task-1",
            action=ProjectControlAction.REDIRECT,
            direction="change course",
        )

    manager.lifecycle_repository.release_project_cycle_claim(claim)
    result = apply_project_control(
        manager,
        task_id="task-1",
        action=ProjectControlAction.REDIRECT,
        direction="change course",
    )
    assert result.direction == "change course"
    manager.close()


def test_project_redirect_survives_concurrent_resume(tmp_path, monkeypatch) -> None:
    manager, _ = _create_project_task(tmp_path)
    apply_project_control(
        manager,
        task_id="task-1",
        action=ProjectControlAction.PAUSE,
    )
    resumer = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    resume_read = Event()
    redirect_done = Event()
    original_get = resumer.lifecycle_repository.get

    def wait_after_resume_read(task_id):
        record = original_get(task_id)
        if not resume_read.is_set():
            resume_read.set()
            assert redirect_done.wait(timeout=5)
        return record

    monkeypatch.setattr(
        resumer.lifecycle_repository,
        "get",
        wait_after_resume_read,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        resumed = executor.submit(
            resumer.transition_task,
            task_id="task-1",
            to_state=TaskLifecycleState.ACTIVE,
        )
        assert resume_read.wait(timeout=5)
        try:
            result = apply_project_control(
                manager,
                task_id="task-1",
                action=ProjectControlAction.REDIRECT,
                direction="change course",
            )
        finally:
            redirect_done.set()
        resumed.result(timeout=5)

    record = manager.get_task("task-1")
    assert record is not None
    assert result.direction == "change course"
    assert record.state == TaskLifecycleState.ACTIVE
    assert record.metadata["operator_direction"] == "change course"
    resumer.close()
    manager.close()


def test_project_redirect_keeps_newest_mixed_guidance_revision(tmp_path) -> None:
    manager, _ = _create_project_task(tmp_path)
    apply_project_control(
        manager,
        task_id="task-1",
        action=ProjectControlAction.REPRIORITIZE,
        priority="verify first",
    )
    apply_project_control(
        manager,
        task_id="task-1",
        action=ProjectControlAction.PAUSE,
    )
    apply_project_control(
        manager,
        task_id="task-1",
        action=ProjectControlAction.REDIRECT,
        direction="prepare the report before packaging",
    )
    record = manager.get_task("task-1")
    checkpoint = load_latest_project_checkpoint(manager, task_id="task-1")
    assert record is not None
    assert checkpoint is not None

    guidance, revision = project_checkpoint_guidance(record.metadata, checkpoint)

    assert guidance == {
        "direction": "prepare the report before packaging",
        "priority": "verify first",
    }
    assert revision == 2
    manager.close()


@pytest.mark.parametrize(
    "state",
    (
        TaskLifecycleState.ACTIVE,
        TaskLifecycleState.CANCELLED,
        TaskLifecycleState.DONE,
        TaskLifecycleState.FAILED,
    ),
)
def test_project_redirect_requires_paused_state(tmp_path, state) -> None:
    manager, _ = _create_project_task(tmp_path)
    if state != TaskLifecycleState.ACTIVE:
        manager.transition_task(task_id="task-1", to_state=state)

    with pytest.raises(ValueError, match="redirect requires a paused project"):
        apply_project_control(
            manager,
            task_id="task-1",
            action=ProjectControlAction.REDIRECT,
            direction="change course",
        )

    record = manager.get_task("task-1")
    assert record is not None
    assert "operator_direction" not in record.metadata
    manager.close()


def test_operator_guidance_rejects_oversized_text_without_revision(tmp_path) -> None:
    manager, _ = _create_project_task(tmp_path)

    with pytest.raises(ValueError, match="priority exceeds 4000 characters"):
        apply_project_control(
            manager,
            task_id="task-1",
            action=ProjectControlAction.REPRIORITIZE,
            priority="x" * 4001,
        )

    record = manager.get_task("task-1")
    assert record is not None
    assert "operator_guidance_revision" not in record.metadata
    assert "priority" not in record.metadata
    manager.close()


def test_operator_guidance_rejects_full_inbox_until_checkpoint_consumes_it(
    tmp_path,
) -> None:
    manager, project_run = _create_project_task(tmp_path)
    for index in range(32):
        apply_project_control(
            manager,
            task_id="task-1",
            action=ProjectControlAction.ANSWER_INPUT,
            input_request_id=f"input-{index}",
            answer=f"answer-{index}",
        )

    with pytest.raises(ValueError, match="operator answers are full"):
        apply_project_control(
            manager,
            task_id="task-1",
            action=ProjectControlAction.ANSWER_INPUT,
            input_request_id="input-33",
            answer="wait for checkpoint consumption",
        )
    record = manager.get_task("task-1")
    assert record is not None
    assert record.metadata["operator_guidance_revision"] == 32
    assert len(record.metadata["operator_answers"]) == 32
    assert record.metadata["operator_answers"][0]["answer"] == "answer-0"

    save_project_run_checkpoint(
        manager,
        project_run,
        checkpoint_id="checkpoint-consumed",
        payload={"operator_guidance_consumed_revision": 32},
    )
    apply_project_control(
        manager,
        task_id="task-1",
        action=ProjectControlAction.ANSWER_INPUT,
        input_request_id="input-33",
        answer="accepted after consumption",
    )
    record = manager.get_task("task-1")
    assert record is not None
    assert record.metadata["operator_guidance_revision"] == 33
    assert record.metadata["operator_answers"] == [
        {
            "request_id": "input-33",
            "answer": "accepted after consumption",
            "revision": 33,
        }
    ]
    manager.close()


def test_extend_budget_rejects_malformed_persisted_extensions(tmp_path) -> None:
    manager, _ = _create_project_task(tmp_path)
    manager.update_task_metadata(
        task_id="task-1",
        metadata={"budget_extensions": "invalid"},
    )

    with pytest.raises(ValueError, match="budget_extensions must be a mapping"):
        apply_project_control(
            manager,
            task_id="task-1",
            action=ProjectControlAction.EXTEND_BUDGET,
            extra_iterations=1,
        )

    record = manager.get_task("task-1")
    assert record is not None
    assert record.metadata["budget_extensions"] == "invalid"
    manager.close()


def test_project_objective_contract_is_strict_and_serializable() -> None:
    contract = ProjectObjectiveContract(
        objective="Build a reusable project worker",
        success_criteria=("objective persists", "resume packet exists"),
        verification=("focused tests pass",),
        milestones=("define contract", "prove projection"),
        constraints=("do not create a parallel goal evaluator",),
    )
    ledger = ProjectObjectiveLedger(
        ledger_ref="artifact:objective.json", contract=contract
    )

    payload = ledger.model_dump(mode="json")

    assert payload["ledger_ref"] == "artifact:objective.json"
    assert payload["contract"]["success_criteria"] == [
        "objective persists",
        "resume packet exists",
    ]
    with pytest.raises(ValueError):
        ProjectObjectiveContract(
            objective="",
            success_criteria=("ok",),
            verification=("focused tests pass",),
        )
    with pytest.raises(ValueError):
        ProjectObjectiveContract(
            objective="Build",
            success_criteria=(),
            verification=("focused tests pass",),
        )


def test_project_run_projection_links_existing_autonomy_task_and_goal() -> None:
    project_run = build_project_run_projection(
        _autonomy_run(),
        task_record=_task_record(),
        objective_ledger_ref="artifact:objective.json",
        evidence_ledger_ref="artifact:evidence.jsonl",
        resume_packet_ref="artifact:resume.json",
        operator_decision_log_ref="artifact:operator-decisions.jsonl",
        capability_plan_ref="artifact:capabilities.json",
        metrics_summary_ref="artifact:metrics.json",
        verification_state=ProjectVerificationState.IN_PROGRESS,
    )

    payload = project_run.model_dump(mode="json")

    assert project_run.project_run_id.startswith("prun_")
    assert project_run.autonomy_run_id
    assert project_run.task_id == "task-1"
    assert project_run.goal_id == "goal-1"
    assert project_run.last_checkpoint_id == "checkpoint-1"
    assert project_run.task_state == TaskLifecycleState.ACTIVE
    assert payload["status"] == "running"
    assert payload["phase"] == "execute"
    assert payload["verification_state"] == "in_progress"


def test_project_run_projection_rejects_unlinked_inputs() -> None:
    with pytest.raises(ValueError, match="task_record.task_id"):
        build_project_run_projection(
            _autonomy_run(),
            task_record=_task_record("other-task"),
            objective_ledger_ref="artifact:objective.json",
            evidence_ledger_ref="artifact:evidence.jsonl",
            resume_packet_ref="artifact:resume.json",
            operator_decision_log_ref="artifact:operator-decisions.jsonl",
            capability_plan_ref="artifact:capabilities.json",
            metrics_summary_ref="artifact:metrics.json",
        )

    with pytest.raises(ValueError, match="autonomy_run.task_id"):
        build_project_run_projection(
            _autonomy_run().model_copy(update={"task_id": None}),
            objective_ledger_ref="artifact:objective.json",
            evidence_ledger_ref="artifact:evidence.jsonl",
            resume_packet_ref="artifact:resume.json",
            operator_decision_log_ref="artifact:operator-decisions.jsonl",
            capability_plan_ref="artifact:capabilities.json",
            metrics_summary_ref="artifact:metrics.json",
        )


def test_project_run_summary_is_human_readable() -> None:
    summary = render_project_run_summary(
        build_project_run_projection(
            _autonomy_run(),
            objective_ledger_ref="artifact:objective.json",
            evidence_ledger_ref="artifact:evidence.jsonl",
            resume_packet_ref="artifact:resume.json",
            operator_decision_log_ref="artifact:operator-decisions.jsonl",
            capability_plan_ref="artifact:capabilities.json",
            metrics_summary_ref="artifact:metrics.json",
        )
    )

    assert "project_run_id: prun_" in summary
    assert "task_id: task-1" in summary
    assert "goal_id: goal-1" in summary
    assert "status: running" in summary
    assert "checkpoint: checkpoint-1" in summary


def test_project_run_checkpoint_survives_lifecycle_manager_restart(tmp_path) -> None:
    db_path = tmp_path / "tasks.db"
    manager = TaskManager.for_lifecycle_db(db_path=db_path)
    manager.create_task(
        session_id="session-1",
        mode_name="project",
        goal="ship a project worker",
        agent_id="agent-1",
        task_id="task-1",
    )
    autonomy_run = _autonomy_run()
    project_run = build_project_run_projection(
        autonomy_run,
        objective_ledger_ref="artifact:objective.json",
        evidence_ledger_ref="artifact:evidence.jsonl",
        resume_packet_ref="artifact:resume.json",
        operator_decision_log_ref="artifact:operator-decisions.jsonl",
        capability_plan_ref="artifact:capabilities.json",
        metrics_summary_ref="artifact:metrics.json",
    )
    initial = save_project_run_checkpoint(
        manager,
        project_run,
        checkpoint_id="checkpoint-1",
        payload=project_checkpoints.initial_repository_lifecycle_payload(
            autonomy_run,
            project_run,
            workspace_boundary_ref="local:/",
        ),
    )
    plan = TaskPlan(
        plan_id="plan-1",
        objective=autonomy_run.goal_text,
        criterion_ids=["criterion-tests"],
        steps=[{"step_id": "build", "description": "Build it"}],
    )
    revision = TaskPlanRevision(
        plan_id="plan-1",
        revision_id="revision-1",
        criterion_ids=["criterion-tests"],
        verifier_refs=("verification:failed",),
        revised_steps=[{"step_id": "build", "description": "Repair it"}],
    )
    turn = ProjectTurnResult(
        summary="repair required",
        evidence_refs=("receipt:git-status",),
        effect_refs=("effect:file-write",),
        tool_call_count=2,
        task_plan=plan,
        task_plan_revision=revision,
    )
    advanced_run = project_run.model_copy(
        update={
            "phase": AutonomyRunPhase.RECOVER,
            "committed_cycle_count": 1,
            "progress_refs": turn.evidence_refs,
            "effect_refs": turn.effect_refs,
            "verifier_refs": ("verification:cycle-1:failed",),
        }
    )
    checkpoint = save_project_run_checkpoint(
        manager,
        advanced_run,
        checkpoint_id="checkpoint-2",
        payload={
            **project_checkpoints.plan_checkpoint_payload(initial, turn),
            **project_checkpoints.advance_repository_lifecycle_payload(
                initial,
                advanced_run,
                turn=turn,
                verification_count=1,
                next_action=ProjectCycleDecision.CONTINUE.value,
            ),
        },
    )

    restarted_manager = TaskManager.for_lifecycle_db(db_path=db_path)
    loaded = load_latest_project_checkpoint(
        restarted_manager,
        task_id="task-1",
    )
    resumed = resume_project_run_from_latest_checkpoint(
        restarted_manager,
        task_id="task-1",
    )
    task = restarted_manager.get_task("task-1")

    assert loaded == checkpoint
    assert resumed.project_run_id == checkpoint.project_run.project_run_id
    assert resumed.last_checkpoint_id == "checkpoint-2"
    assert loaded is not None
    lifecycle = loaded.payload["repository_lifecycle"]
    assert set(lifecycle) == {
        project_run.objective_ledger_ref,
        project_run.evidence_ledger_ref,
        project_run.resume_packet_ref,
        project_run.operator_decision_log_ref,
        project_run.capability_plan_ref,
        project_run.metrics_summary_ref,
    }
    assert lifecycle[project_run.objective_ledger_ref]["source_revisions"] == {
        "execution_repository": "abc"
    }
    assert lifecycle[project_run.evidence_ledger_ref]["receipts"] == [
        "receipt:git-status",
        "effect:file-write",
        "verification:cycle-1:failed",
    ]
    assert lifecycle[project_run.resume_packet_ref] == {
        "workspace_boundary": "local:/",
        "execution_repository": project_run.workspace_ref,
        "task_id": "task-1",
        "active_tracker_row": None,
        "current_revisions": {
            "execution_repository": "abc",
            "task_plan": "revision-1",
        },
        "current_phase": "recover",
        "next_action": "continue",
        "task_plan_required": False,
        "expected_checks": [],
    }
    assert loaded.payload["task_plan_revision"]["revision_id"] == "revision-1"
    assert lifecycle[project_run.metrics_summary_ref] == {
        "cycle_count": 1,
        "tool_call_count": 2,
        "verification_count": 1,
    }
    assert task is not None
    assert task.metadata["resume_count"] == 1
    assert task.metadata["last_resume_checkpoint_id"] == "checkpoint-2"


def test_repository_lifecycle_payload_bounds_and_redacts_text() -> None:
    autonomy_run = _autonomy_run().model_copy(
        update={"goal_text": f"token=super-secret {'x' * 5_000}"}
    )
    project_run = build_project_run_projection(
        autonomy_run,
        objective_ledger_ref="artifact:objective.json",
        evidence_ledger_ref="artifact:evidence.jsonl",
        resume_packet_ref="artifact:resume.json",
        operator_decision_log_ref="artifact:operator-decisions.jsonl",
        capability_plan_ref="artifact:capabilities.json",
        metrics_summary_ref="artifact:metrics.json",
    )

    lifecycle = project_checkpoints.initial_repository_lifecycle_payload(
        autonomy_run,
        project_run,
    )["repository_lifecycle"]
    objective = lifecycle[project_run.objective_ledger_ref]["objective"]

    assert len(objective) == 4_000
    assert "super-secret" not in objective
    assert "[REDACTED]" in objective


def test_project_checkpoint_applies_public_task_plan_terminal_signals(tmp_path) -> None:
    manager, project_run = _create_project_task(tmp_path)
    initial = save_project_run_checkpoint(
        manager,
        project_run,
        checkpoint_id="checkpoint-plan-signals",
        payload=project_checkpoints.initial_repository_lifecycle_payload(
            _autonomy_run(),
            project_run,
            task_plan_required=True,
        ),
    )
    plan = TaskPlan(
        plan_id="plan-1",
        objective="Ship",
        steps=[{"step_id": "build", "description": "Build it"}],
    )
    completed_payload = project_checkpoints.plan_checkpoint_payload(
        initial,
        ProjectTurnResult(
            summary="done",
            task_plan=plan,
            task_plan_step_completed=TaskPlanStepCompleted(
                plan_id="plan-1",
                step_id="build",
                output_summary="built",
            ),
            task_plan_completed=TaskPlanTerminalSignal(plan_id="plan-1"),
        ),
    )
    abandoned_payload = project_checkpoints.plan_checkpoint_payload(
        initial,
        ProjectTurnResult(
            summary="blocked",
            task_plan=plan,
            task_plan_step_blocked=TaskPlanStepBlocked(
                plan_id="plan-1",
                step_id="build",
                blocker_type="operator",
            ),
            task_plan_abandoned=TaskPlanTerminalSignal(plan_id="plan-1"),
        ),
    )

    assert completed_payload["task_plan"]["status"] == "completed"
    assert completed_payload["task_plan"]["steps"][0]["status"] == "completed"
    assert abandoned_payload["task_plan"]["status"] == "abandoned"
    assert abandoned_payload["task_plan"]["steps"][0]["status"] == "blocked"


def test_project_checkpoint_keeps_progress_after_same_turn_revision(tmp_path) -> None:
    manager, project_run = _create_project_task(tmp_path)
    initial = save_project_run_checkpoint(
        manager,
        project_run,
        checkpoint_id="checkpoint-plan-revision-progress",
        payload=project_checkpoints.initial_repository_lifecycle_payload(
            _autonomy_run(),
            project_run,
            task_plan_required=True,
        ),
    )
    current_plan = TaskPlan(
        plan_id="plan-1",
        objective="Ship",
        steps=[
            {
                "step_id": "build",
                "description": "Build it",
                "status": "completed",
            }
        ],
    )
    revision = TaskPlanRevision(
        plan_id="plan-1",
        revision_id="revision-1",
        verifier_refs=["verification:failed"],
        revised_steps=[{"step_id": "build", "description": "Repair it"}],
    )

    payload = project_checkpoints.plan_checkpoint_payload(
        initial,
        ProjectTurnResult(
            summary="done",
            task_plan=current_plan,
            task_plan_revision=revision,
            task_plan_completed=TaskPlanTerminalSignal(plan_id="plan-1"),
        ),
    )

    assert payload["task_plan"]["status"] == "completed"
    assert payload["task_plan"]["steps"][0]["status"] == "completed"
    assert payload["task_plan_revision"]["revision_id"] == "revision-1"


def test_project_run_rejects_duplicate_open_worker(tmp_path) -> None:
    manager = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    manager.create_task(
        session_id="session-1",
        mode_name="project",
        goal="ship a project worker",
        agent_id="agent-1",
        task_id="task-1",
    )
    manager.create_task(
        session_id="session-1",
        mode_name="project",
        goal="ship a project worker again",
        agent_id="agent-1",
        task_id="task-2",
    )
    project_run = build_project_run_projection(
        _autonomy_run(),
        objective_ledger_ref="artifact:objective.json",
        evidence_ledger_ref="artifact:evidence.jsonl",
        resume_packet_ref="artifact:resume.json",
        operator_decision_log_ref="artifact:operator-decisions.jsonl",
        capability_plan_ref="artifact:capabilities.json",
        metrics_summary_ref="artifact:metrics.json",
    )
    save_project_run_checkpoint(
        manager,
        project_run,
        checkpoint_id="checkpoint-1",
    )

    duplicate = project_run.model_copy(update={"task_id": "task-2"})

    assert (
        find_open_project_worker(
            manager,
            project_run_id=project_run.project_run_id,
        ).task_id
        == "task-1"
    )
    with pytest.raises(ValueError, match="open project worker already exists"):
        save_project_run_checkpoint(
            manager,
            duplicate,
            checkpoint_id="checkpoint-2",
        )


def test_project_cycle_records_and_replays_from_checkpoints(tmp_path) -> None:
    db_path = tmp_path / "tasks.db"
    manager = TaskManager.for_lifecycle_db(db_path=db_path)
    manager.create_task(
        session_id="session-1",
        mode_name="project",
        goal="ship a project worker",
        agent_id="agent-1",
        task_id="task-1",
    )
    project_run = build_project_run_projection(
        _autonomy_run(),
        objective_ledger_ref="artifact:objective.json",
        evidence_ledger_ref="artifact:evidence.jsonl",
        resume_packet_ref="artifact:resume.json",
        operator_decision_log_ref="artifact:operator-decisions.jsonl",
        capability_plan_ref="artifact:capabilities.json",
        metrics_summary_ref="artifact:metrics.json",
    )

    cycle = record_project_cycle(
        manager,
        project_run,
        cycle_id="cycle-1",
        milestone="define contract",
        intended_action="add project run projection",
        evidence_refs=("artifact:evidence.jsonl#cycle-1",),
        validation_refs=("pytest:tests/task/test_project_run.py",),
        decision=ProjectCycleDecision.CONTINUE,
        payload={"notes": "first cycle"},
    )

    restarted_manager = TaskManager.for_lifecycle_db(db_path=db_path)
    replayed = replay_project_cycles(restarted_manager, task_id="task-1")
    latest = load_latest_project_checkpoint(restarted_manager, task_id="task-1")

    assert replayed == (cycle,)
    assert latest is not None
    assert latest.payload["cycle"]["milestone"] == "define contract"
    assert restarted_manager.get_checkpoint("task-1", cycle.checkpoint_id) is not None


def test_project_cycle_requires_reason_for_terminal_decisions(tmp_path) -> None:
    manager = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    manager.create_task(
        session_id="session-1",
        mode_name="project",
        goal="ship a project worker",
        agent_id="agent-1",
        task_id="task-1",
    )

    with pytest.raises(ValueError, match="decision_reason"):
        record_project_cycle(
            manager,
            build_project_run_projection(
                _autonomy_run(),
                objective_ledger_ref="artifact:objective.json",
                evidence_ledger_ref="artifact:evidence.jsonl",
                resume_packet_ref="artifact:resume.json",
                operator_decision_log_ref="artifact:operator-decisions.jsonl",
                capability_plan_ref="artifact:capabilities.json",
                metrics_summary_ref="artifact:metrics.json",
            ),
            cycle_id="cycle-1",
            milestone="blocked",
            intended_action="wait for operator",
            evidence_refs=("artifact:evidence.jsonl#cycle-1",),
            validation_refs=("pytest:tests/task/test_project_run.py",),
            decision=ProjectCycleDecision.BLOCKED,
        )


def test_project_policy_state_survives_restart_and_denies_before_grants(
    tmp_path,
) -> None:
    manager, project_run = _create_project_task(tmp_path)
    state = build_project_policy_state(
        manager,
        task_id=project_run.task_id,
        denied_tool_names=("exec.run",),
        budget=ProjectBudgetPolicy(
            max_iterations=3,
            max_wall_clock_ms=1000,
            max_tool_calls=2,
            max_tokens=200,
            unattended=True,
        ),
    )
    save_project_policy_state(manager, state)
    issue_project_permission_grant(
        manager,
        task_id=project_run.task_id,
        grant_id="grant-1",
        tool_name="exec.run",
        scope="workspace",
        issued_at_ms=100,
        expires_at_ms=1000,
        destructive_allowed=True,
    )

    restarted = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    loaded = load_project_policy_state(restarted, task_id=project_run.task_id)
    decision = evaluate_project_permission(
        restarted,
        task_id=project_run.task_id,
        tool_name="exec.run",
        scope="workspace",
        destructive=True,
        at_ms=200,
    )

    assert loaded is not None
    assert loaded.project_run_id == project_run.project_run_id
    assert loaded.budget.unattended is True
    assert decision.decision == ProjectPermissionDecision.DENIED
    assert decision.allowed is False
    assert decision.reason == "tool is denied by project policy"


def test_project_permission_grant_expiry_destructive_and_use_limit(tmp_path) -> None:
    manager, project_run = _create_project_task(tmp_path)
    save_project_policy_state(
        manager,
        build_project_policy_state(
            manager,
            task_id=project_run.task_id,
            budget=ProjectBudgetPolicy(destructive_requires_confirmation=True),
        ),
    )
    issue_project_permission_grant(
        manager,
        task_id=project_run.task_id,
        grant_id="grant-1",
        tool_name="file.write",
        scope="workspace",
        issued_at_ms=100,
        expires_at_ms=1000,
        max_uses=1,
    )

    destructive_decision = evaluate_project_permission(
        manager,
        task_id=project_run.task_id,
        tool_name="file.write",
        scope="workspace",
        destructive=True,
        at_ms=200,
    )
    read_decision = evaluate_project_permission(
        manager,
        task_id=project_run.task_id,
        tool_name="file.write",
        scope="workspace",
        at_ms=200,
    )
    consume_project_permission_grant(
        manager,
        task_id=project_run.task_id,
        grant_id="grant-1",
    )
    exhausted_decision = evaluate_project_permission(
        manager,
        task_id=project_run.task_id,
        tool_name="file.write",
        scope="workspace",
        at_ms=300,
    )
    expired_decision = evaluate_project_permission(
        manager,
        task_id=project_run.task_id,
        tool_name="file.write",
        scope="workspace",
        at_ms=1200,
    )

    assert destructive_decision.decision == ProjectPermissionDecision.DENIED
    assert "destructive" in destructive_decision.reason
    assert read_decision.decision == ProjectPermissionDecision.ALLOWED
    assert read_decision.grant_id == "grant-1"
    assert exhausted_decision.decision == ProjectPermissionDecision.EXPIRED
    assert exhausted_decision.reason == "grant use limit exhausted"
    assert expired_decision.decision == ProjectPermissionDecision.EXPIRED


def test_project_budget_policy_uses_operator_extensions(tmp_path) -> None:
    manager, project_run = _create_project_task(tmp_path)
    save_project_policy_state(
        manager,
        build_project_policy_state(
            manager,
            task_id=project_run.task_id,
            budget=ProjectBudgetPolicy(
                max_iterations=2,
                max_wall_clock_ms=1000,
                max_tool_calls=2,
                max_tokens=50,
            ),
        ),
    )

    within = evaluate_project_budget(
        manager,
        task_id=project_run.task_id,
        iterations=2,
        wall_clock_ms=900,
        tool_calls=2,
        tokens=50,
    )
    exceeded = evaluate_project_budget(
        manager,
        task_id=project_run.task_id,
        iterations=3,
    )
    apply_project_control(
        manager,
        task_id=project_run.task_id,
        action=ProjectControlAction.EXTEND_BUDGET,
        extra_iterations=2,
        extra_tool_calls=1,
    )
    extended = evaluate_project_budget(
        manager,
        task_id=project_run.task_id,
        iterations=3,
        tool_calls=3,
    )

    assert within.decision == ProjectPermissionDecision.ALLOWED
    assert within.remaining["iterations"] == 0
    assert exceeded.decision == ProjectPermissionDecision.BUDGET_EXCEEDED
    assert exceeded.reason == "iterations budget exceeded"
    assert extended.decision == ProjectPermissionDecision.ALLOWED
    assert extended.limits["iterations"] == 4
    assert extended.limits["tool_calls"] == 3


def test_project_capability_matrix_consumes_gap_assessment_rows() -> None:
    matrix = build_project_capability_matrix(project_run_id="prun_1")

    website = matrix.row_for(ProjectCapabilityArea.WEBSITE_APP_BUILD)
    image = matrix.row_for(ProjectCapabilityArea.IMAGE_INPUT)
    desktop = matrix.row_for(ProjectCapabilityArea.DESKTOP_APPS)

    assert website.owner_ref == GAP_ASSESSMENT_REF
    assert GAP_ASSESSMENT_REF in website.evidence_refs
    assert website.disposition == ProjectCapabilityDisposition.NOT_REQUIRED_FOR_PILOT
    assert image.disposition == ProjectCapabilityDisposition.AVAILABLE
    assert image.needed_for_pilot is True
    assert desktop.owner_ref == GAP_ASSESSMENT_REF
    assert desktop.disposition == ProjectCapabilityDisposition.NOT_REQUIRED_FOR_PILOT


def test_project_capability_matrix_marks_required_gaps_explicitly() -> None:
    matrix = build_project_capability_matrix(
        project_run_id="prun_1",
        pilot_areas={
            ProjectCapabilityArea.DESKTOP_APPS,
            ProjectCapabilityArea.EMAIL,
            ProjectCapabilityArea.CODE_EDITS,
        },
    )
    desktop = matrix.row_for(ProjectCapabilityArea.DESKTOP_APPS)
    email = matrix.row_for(ProjectCapabilityArea.EMAIL)
    code = matrix.row_for(ProjectCapabilityArea.CODE_EDITS)
    blockers = capability_rows_requiring_resolution(matrix)

    assert desktop.disposition == ProjectCapabilityDisposition.BLOCKER
    assert desktop.blocker == (
        "desktop_apps is required for this pilot but has no first-class owner."
    )
    assert email.disposition == ProjectCapabilityDisposition.DEFER_OWNED
    assert email.defer_owner == "tool-skill-domain-owner"
    assert DOMAIN_WORKFLOW_REF in email.evidence_refs
    assert code.disposition == ProjectCapabilityDisposition.AVAILABLE
    assert {row.area for row in blockers} == {
        ProjectCapabilityArea.DESKTOP_APPS,
        ProjectCapabilityArea.EMAIL,
    }


def test_project_capability_matrix_render_is_operator_readable() -> None:
    matrix = build_project_capability_matrix(
        project_run_id="prun_1",
        pilot_areas={ProjectCapabilityArea.TTS},
    )

    rendered = render_project_capability_matrix(matrix)

    assert "project_run_id: prun_1" in rendered
    assert "* tts: missing / blocked-capability-gap" in rendered
    assert "blocker: tts is required for this pilot" in rendered
    assert "- code_edits: supported / not_required_for_pilot" in rendered


def test_project_report_includes_outcome_metrics_and_baseline_comparison() -> None:
    project_run = build_project_run_projection(
        _autonomy_run(),
        objective_ledger_ref="artifact:objective.json",
        evidence_ledger_ref="artifact:evidence.jsonl",
        resume_packet_ref="artifact:resume.json",
        operator_decision_log_ref="artifact:operator-decisions.jsonl",
        capability_plan_ref="artifact:capabilities.json",
        metrics_summary_ref="artifact:metrics.json",
        verification_state=ProjectVerificationState.VERIFIED,
    )
    baseline = ProjectMetricSnapshot(
        active_work_ms=1000,
        duplicate_tool_call_count=4,
        proof_packet_completeness_percent=25.0,
    )
    current = ProjectMetricSnapshot(
        active_work_ms=800,
        duplicate_tool_call_count=1,
        proof_packet_completeness_percent=100.0,
        verification_pass_count=2,
    )
    report = build_project_report(
        project_run,
        metrics=current,
        baseline_metrics=baseline,
        capability_matrix=build_project_capability_matrix(
            project_run_id=project_run.project_run_id,
        ),
        proof_refs=("artifact:evidence.jsonl",),
        safety_notes=("deny-first permission policy passed",),
        ux_notes=("operator report is text-renderable",),
    )

    comparisons = {
        comparison.metric: comparison for comparison in report.baseline_comparisons
    }
    rendered = render_project_report(report)

    assert report.outcome == ProjectOutcomeClassification.COMPLETED_VERIFIED
    assert report.metrics.verification_pass_count == 2
    assert comparisons["active_work_ms"].delta == -200.0
    assert comparisons["duplicate_tool_call_count"].delta == -3.0
    assert comparisons["proof_packet_completeness_percent"].delta == 75.0
    assert "outcome: completed-verified" in rendered
    assert "baseline_comparisons:" in rendered
    assert "capabilities: 16 rows" in rendered
    assert "deny-first permission policy passed" in rendered


def test_project_report_renders_unmeasured_metrics_as_unknown() -> None:
    project_run = build_project_run_projection(
        _autonomy_run(),
        objective_ledger_ref="artifact:objective.json",
        evidence_ledger_ref="artifact:evidence.jsonl",
        resume_packet_ref="artifact:resume.json",
        operator_decision_log_ref="artifact:operator-decisions.jsonl",
        capability_plan_ref="artifact:capabilities.json",
        metrics_summary_ref="artifact:metrics.json",
    )

    report = build_project_report(project_run, metrics=ProjectMetricSnapshot())
    rendered = render_project_report(report)

    assert report.baseline_comparisons == ()
    assert "active_work_ms: unknown" in rendered
    assert "operator_intervention_count: unknown" in rendered
