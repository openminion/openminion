from __future__ import annotations

import pytest

from openminion.modules.task import (
    AutonomyRunPhase,
    AutonomyRunStatus,
    CPACK_TRACKER_REF,
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
    render_project_report,
    render_project_run_summary,
    replay_project_cycles,
    resume_project_run_from_latest_checkpoint,
    save_project_policy_state,
    save_project_run_checkpoint,
)


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
    checkpoint = save_project_run_checkpoint(
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
        checkpoint_id="checkpoint-2",
        payload={"milestone": "contracts"},
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
    assert task is not None
    assert task.metadata["resume_count"] == 1
    assert task.metadata["last_resume_checkpoint_id"] == "checkpoint-2"


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
    assert email.defer_owner == "CPACK"
    assert CPACK_TRACKER_REF in email.evidence_refs
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
