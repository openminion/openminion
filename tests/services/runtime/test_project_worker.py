from __future__ import annotations

import json
from pathlib import Path

import pytest

from openminion.modules.brain.loop.strategies.coding.contracts import (
    PROJECT_CODING_ALLOWED_TOOLS,
    PROJECT_RELEASE_ADDITIONAL_TOOLS,
)
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
from openminion.modules.task.plan import (
    TaskPlan,
    TaskPlanRevision,
    TaskPlanStepCompleted,
    TaskPlanTerminalSignal,
)
from openminion.modules.task.project import checkpoints as project_checkpoints
from openminion.modules.task.project.checkpoints import plan_checkpoint_payload
from openminion.modules.task.project.effects import (
    ProjectEffectRecord,
    ProjectEffectStatus,
    load_project_effect_receipt,
    load_project_effect_record,
    save_project_effect_record,
)
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


def _project(
    tmp_path,
    *,
    max_iterations: int = 3,
    task_plan_required: bool = False,
    expected_checks: tuple[str, ...] = (),
    launch_approved: bool = False,
    release_tools_approved: bool = False,
):
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
        payload={
            "decision": "continue",
            "replan_count": 0,
            **project_checkpoints.initial_repository_lifecycle_payload(
                run,
                project_run,
                task_plan_required=task_plan_required,
                expected_checks=expected_checks,
                launch_approved=launch_approved,
                release_tools_approved=release_tools_approved,
            ),
        },
    )
    return store, manager, run


def _save_ci_effect(
    manager: TaskManager,
    *,
    effect_id: str,
    capability_ref: str,
    receipt: dict[str, object],
) -> None:
    save_project_effect_record(
        manager,
        ProjectEffectRecord(
            effect_id=effect_id,
            task_id="task-1",
            idempotency_key=effect_id,
            actor_ref="agent:agent-1",
            capability_ref=capability_ref,
            precondition_refs=(f"precondition:{effect_id}",),
            result_ref=f"result:{effect_id}",
            non_reversible_reason="Remote repository state is retained.",
            status=ProjectEffectStatus.SUCCEEDED,
        ),
        receipt=receipt,
    )


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


def test_approved_project_turn_uses_the_core_tool_scope(tmp_path) -> None:
    store, manager, run = _project(tmp_path, launch_approved=True)
    requests: list[ProjectTurnRequest] = []
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda request: (
            requests.append(request) or ProjectTurnResult(summary="worked")
        ),
        verify=lambda: (_evidence(_TestEvidenceStatus.FAILED),),
    )

    worker.run_cycle(run.run_id)

    assert set(requests[0].allowed_tools) == PROJECT_CODING_ALLOWED_TOOLS
    assert set(requests[0].allowed_tools).isdisjoint(
        PROJECT_RELEASE_ADDITIONAL_TOOLS
    )


def test_separately_approved_release_project_turn_uses_release_scope(tmp_path) -> None:
    store, manager, run = _project(
        tmp_path,
        launch_approved=True,
        release_tools_approved=True,
    )
    requests: list[ProjectTurnRequest] = []
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda request: (
            requests.append(request) or ProjectTurnResult(summary="worked")
        ),
        verify=lambda: (_evidence(_TestEvidenceStatus.FAILED),),
    )

    worker.run_cycle(run.run_id)

    assert set(requests[0].allowed_tools) == (
        PROJECT_CODING_ALLOWED_TOOLS | PROJECT_RELEASE_ADDITIONAL_TOOLS
    )


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


def test_project_cycle_preserves_effect_state_across_restart(tmp_path) -> None:
    store, manager, run = _project(tmp_path)
    effect = ProjectEffectRecord(
        effect_id="effect:github.open_pr:open-pr-1",
        task_id="task-1",
        idempotency_key="open-pr-1",
        actor_ref="agent:agent-1",
        capability_ref="github.open_pr",
        precondition_refs=("github:repository:openminion/example",),
        result_ref="github:pull:openminion/example#17",
        non_reversible_reason="The pull request remains open.",
        status=ProjectEffectStatus.SUCCEEDED,
    )
    receipt = {
        "owner": "openminion",
        "repo": "example",
        "number": 17,
        "head_sha": "abc1234",
    }

    def turn(_request: ProjectTurnRequest) -> ProjectTurnResult:
        save_project_effect_record(manager, effect, receipt=receipt)
        return ProjectTurnResult(summary="opened pull request")

    result = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=turn,
        verify=lambda: (_evidence(_TestEvidenceStatus.FAILED),),
        owner_id="worker-1",
    ).run_cycle(run.run_id)

    assert effect.effect_id in result.project_run.effect_refs
    manager.close()
    restarted = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    loaded = load_project_effect_record(
        restarted,
        task_id="task-1",
        effect_id=effect.effect_id,
    )
    assert loaded is not None and loaded.status == ProjectEffectStatus.SUCCEEDED
    assert (
        load_project_effect_receipt(
            restarted,
            task_id="task-1",
            effect_id=effect.effect_id,
        )
        == receipt
    )


def test_project_checks_wait_fail_repair_and_pass_across_restart(tmp_path) -> None:
    expected_checks = ("lint", "tests (3.11)")
    first_head = "a" * 40
    repaired_head = "b" * 40
    store, manager, run = _project(
        tmp_path,
        max_iterations=3,
        expected_checks=expected_checks,
    )
    prompts: list[str] = []
    active_manager = [manager]

    def turn(request: ProjectTurnRequest) -> ProjectTurnResult:
        prompts.append(request.prompt)
        if len(prompts) == 1:
            _save_ci_effect(
                active_manager[0],
                effect_id="effect:git.push:initial",
                capability_ref="git.push",
                receipt={
                    "repository": str(tmp_path),
                    "remote": "origin",
                    "ref": "refs/heads/feature",
                    "remote_oid": first_head,
                },
            )
            _save_ci_effect(
                active_manager[0],
                effect_id="effect:github.open_pr:1",
                capability_ref="github.open_pr",
                receipt={
                    "owner": "openminion",
                    "repo": "example",
                    "number": 17,
                    "head": "feature",
                    "base": "dev",
                    "head_sha": first_head,
                },
            )
        elif len(prompts) == 2:
            assert '"overall_result": "failure"' in request.prompt
            assert '"name": "tests (3.11)"' in request.prompt
            _save_ci_effect(
                active_manager[0],
                effect_id="effect:git.push:repair",
                capability_ref="git.push",
                receipt={
                    "tool_name": "git.push",
                    "repository": str(tmp_path),
                    "remote": "origin",
                    "source_ref": "HEAD",
                    "ref": "refs/heads/feature",
                    "expected_oid": repaired_head,
                    "previous_remote_oid": first_head,
                    "remote_oid": repaired_head,
                    "expected_target_oid": None,
                    "remote_target_oid": None,
                },
            )
        return ProjectTurnResult(summary="worked")

    responses = iter(
        (
            {
                "head_sha": first_head,
                "overall_result": "pending",
                "expected_checks": list(expected_checks),
                "missing_expected_checks": ["tests (3.11)"],
                "failure_facts": [],
            },
            {
                "head_sha": first_head,
                "overall_result": "failure",
                "expected_checks": list(expected_checks),
                "missing_expected_checks": [],
                "failure_facts": [
                    {
                        "name": "tests (3.11)",
                        "conclusion": "failure",
                        "url": "https://github.example/check/1",
                        "output_title": "tests failed",
                        "output_summary": "one failure",
                        "output_text": "assertion failed",
                        "expected": True,
                    }
                ],
            },
            {
                "head_sha": repaired_head,
                "overall_result": "pending",
                "expected_checks": list(expected_checks),
                "missing_expected_checks": ["lint"],
                "failure_facts": [],
            },
            {
                "head_sha": repaired_head,
                "overall_result": "success",
                "expected_checks": list(expected_checks),
                "missing_expected_checks": [],
                "failure_facts": [],
            },
        )
    )

    def fetch(args: object) -> dict[str, object]:
        response = next(responses)
        assert args == {
            "owner": "openminion",
            "repo": "example",
            "head_sha": response["head_sha"],
            "expected_checks": list(expected_checks),
        }
        return response

    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=turn,
        verify=lambda: (_evidence(_TestEvidenceStatus.PASSED),),
        owner_id="worker-1",
        fetch_checks=fetch,
    )
    opened = worker.run_cycle(run.run_id, triggering_cron_job_id="wake:0")
    early = worker.run_cycle(run.run_id)
    stale = worker.run_cycle(run.run_id, triggering_cron_job_id="wrong-wake")
    pending = worker.run_cycle(
        run.run_id,
        triggering_cron_job_id=opened.project_run.next_wake_job_id,
    )

    assert opened.decision == ProjectCycleDecision.CONTINUE
    assert early.reconciled_only is True
    assert early.project_run == opened.project_run
    assert stale.reconciled_only is True
    assert stale.project_run == opened.project_run
    assert opened.check_events[-1]["overall_result"] == "pending"
    assert pending.project_run.committed_cycle_count == 1
    assert pending.check_events[-1]["detail_code"] == "waiting_for_checks"
    assert len(prompts) == 1
    pending_job_id = pending.project_run.next_wake_job_id

    manager.close()
    restarted = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    active_manager[0] = restarted
    worker = ProjectWorker(
        task_manager=restarted,
        autonomy_store=store,
        turn=turn,
        verify=lambda: (_evidence(_TestEvidenceStatus.PASSED),),
        owner_id="worker-2",
        fetch_checks=fetch,
    )
    repaired = worker.run_cycle(
        run.run_id,
        triggering_cron_job_id=pending_job_id,
    )
    repair_pending = worker.run_cycle(
        run.run_id,
        triggering_cron_job_id=repaired.project_run.next_wake_job_id,
    )
    passed = worker.run_cycle(
        run.run_id,
        triggering_cron_job_id=repair_pending.project_run.next_wake_job_id,
    )
    replay = worker.run_cycle(
        run.run_id,
        triggering_cron_job_id=repair_pending.project_run.next_wake_job_id,
    )

    assert [event["overall_result"] for event in repaired.check_events] == [
        "failure",
        "pending",
    ]
    assert repaired.project_run.committed_cycle_count == 2
    assert repair_pending.check_events[-1]["overall_result"] == "pending"
    assert first_head not in repair_pending.project_run.next_wake_job_id
    assert repaired_head in repair_pending.project_run.next_wake_job_id
    assert passed.decision == ProjectCycleDecision.STOP
    assert passed.run.status == AutonomyRunStatus.COMPLETED
    assert passed.check_events[-1]["overall_result"] == "success"
    assert replay.reconciled_only is True
    assert len(prompts) == 3
    checkpoint = load_latest_project_checkpoint(restarted, task_id="task-1")
    assert checkpoint is not None
    observation = checkpoint.payload["repository_lifecycle"][
        checkpoint.project_run.resume_packet_ref
    ]["ci_observation"]
    assert checkpoint.project_run.effect_refs == (
        "effect:git.push:initial",
        "effect:github.open_pr:1",
        "effect:git.push:repair",
    )
    assert observation["head_sha"] == repaired_head
    assert observation["overall_result"] == "success"


def test_failed_project_checks_block_without_an_explicit_repair_push(tmp_path) -> None:
    head = "e" * 40
    store, manager, run = _project(tmp_path, expected_checks=("lint",))
    turn_count = 0

    def turn(_request: ProjectTurnRequest) -> ProjectTurnResult:
        nonlocal turn_count
        turn_count += 1
        if turn_count == 1:
            _save_ci_effect(
                manager,
                effect_id="effect:git.push:initial",
                capability_ref="git.push",
                receipt={
                    "repository": str(tmp_path),
                    "remote": "origin",
                    "ref": "refs/heads/feature",
                    "remote_oid": head,
                },
            )
            _save_ci_effect(
                manager,
                effect_id="effect:github.open_pr:failure",
                capability_ref="github.open_pr",
                receipt={
                    "owner": "openminion",
                    "repo": "example",
                    "number": 17,
                    "head": "feature",
                    "base": "dev",
                    "head_sha": head,
                },
            )
        return ProjectTurnResult(summary="diagnosed")

    results = iter(
        (
            {
                "head_sha": head,
                "overall_result": "pending",
                "expected_checks": ["lint"],
                "missing_expected_checks": ["lint"],
                "failure_facts": [],
            },
            {
                "head_sha": head,
                "overall_result": "failure",
                "expected_checks": ["lint"],
                "missing_expected_checks": [],
                "failure_facts": [{"name": "lint", "conclusion": "failure"}],
            },
        )
    )
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=turn,
        verify=lambda: (_evidence(_TestEvidenceStatus.PASSED),),
        fetch_checks=lambda _args: next(results),
    )

    opened = worker.run_cycle(run.run_id, triggering_cron_job_id="wake:0")
    pending = worker.run_cycle(
        run.run_id,
        triggering_cron_job_id=opened.project_run.next_wake_job_id,
    )
    failed = worker.run_cycle(
        run.run_id,
        triggering_cron_job_id=pending.project_run.next_wake_job_id,
    )

    assert failed.decision == ProjectCycleDecision.BLOCKED
    assert failed.run.status == AutonomyRunStatus.BLOCKED
    assert failed.project_run.next_wake_job_id is None
    assert failed.check_events[-1]["overall_result"] == "failure"
    assert manager.get_task("task-1").state == TaskLifecycleState.PAUSED
    assert turn_count == 2


def test_repair_push_must_use_the_remote_bound_to_the_pull_request(tmp_path) -> None:
    first_head = "f" * 40
    repaired_head = "9" * 40
    store, manager, run = _project(tmp_path, expected_checks=("lint",))
    turn_count = 0

    def turn(_request: ProjectTurnRequest) -> ProjectTurnResult:
        nonlocal turn_count
        turn_count += 1
        if turn_count == 1:
            _save_ci_effect(
                manager,
                effect_id="effect:git.push:initial",
                capability_ref="git.push",
                receipt={
                    "repository": str(tmp_path),
                    "remote": "origin",
                    "ref": "refs/heads/feature",
                    "remote_oid": first_head,
                },
            )
            _save_ci_effect(
                manager,
                effect_id="effect:github.open_pr:remote",
                capability_ref="github.open_pr",
                receipt={
                    "owner": "openminion",
                    "repo": "example",
                    "number": 17,
                    "head": "feature",
                    "base": "dev",
                    "head_sha": first_head,
                },
            )
        else:
            _save_ci_effect(
                manager,
                effect_id="effect:git.push:backup",
                capability_ref="git.push",
                receipt={
                    "repository": str(tmp_path),
                    "remote": "backup",
                    "ref": "refs/heads/feature",
                    "remote_oid": repaired_head,
                },
            )
        return ProjectTurnResult(summary="worked")

    checks = iter(
        (
            {
                "head_sha": first_head,
                "overall_result": "failure",
                "expected_checks": ["lint"],
                "missing_expected_checks": [],
                "failure_facts": [{"name": "lint", "conclusion": "failure"}],
            },
        )
    )
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=turn,
        verify=lambda: (_evidence(_TestEvidenceStatus.PASSED),),
        fetch_checks=lambda _args: next(checks),
    )

    opened = worker.run_cycle(run.run_id, triggering_cron_job_id="wake:0")
    failed = worker.run_cycle(
        run.run_id,
        triggering_cron_job_id=opened.project_run.next_wake_job_id,
    )

    assert failed.decision == ProjectCycleDecision.BLOCKED
    checkpoint = load_latest_project_checkpoint(manager, task_id="task-1")
    assert checkpoint is not None
    observation = project_checkpoints.repository_check_observation(checkpoint)
    assert observation is not None
    assert observation["remote"] == "origin"
    assert observation["head_sha"] == first_head


def test_repository_check_duration_uses_persisted_observation_start(
    tmp_path,
    monkeypatch,
) -> None:
    head = "8" * 40
    _store, manager, _run = _project(tmp_path, expected_checks=("lint",))
    _save_ci_effect(
        manager,
        effect_id="effect:git.push:duration",
        capability_ref="git.push",
        receipt={
            "repository": str(tmp_path),
            "remote": "origin",
            "ref": "refs/heads/feature",
            "remote_oid": head,
        },
    )
    _save_ci_effect(
        manager,
        effect_id="effect:github.open_pr:duration",
        capability_ref="github.open_pr",
        receipt={
            "owner": "openminion",
            "repo": "example",
            "number": 17,
            "head": "feature",
            "base": "dev",
            "head_sha": head,
        },
    )
    checkpoint = load_latest_project_checkpoint(manager, task_id="task-1")
    assert checkpoint is not None
    monkeypatch.setattr(project_checkpoints, "now_ms", lambda: 100)
    checkpoint, started = project_checkpoints.begin_repository_check_observation(
        checkpoint
    )
    assert started is True
    monkeypatch.setattr(project_checkpoints, "now_ms", lambda: 160)
    checkpoint = project_checkpoints.record_repository_check_result(
        checkpoint,
        {
            "head_sha": head,
            "overall_result": "pending",
            "expected_checks": ["lint"],
            "missing_expected_checks": ["lint"],
            "failure_facts": [],
        },
    )

    assert project_checkpoints.repository_check_event(checkpoint)[
        "wait_duration_ms"
    ] == 60


def test_cancelled_project_does_not_recheck_or_repeat_repository_effect(
    tmp_path,
) -> None:
    store, manager, run = _project(
        tmp_path,
        expected_checks=("lint",),
    )

    def open_pull_request(_request: ProjectTurnRequest) -> ProjectTurnResult:
        _save_ci_effect(
            manager,
            effect_id="effect:git.push:cancel",
            capability_ref="git.push",
            receipt={
                "repository": str(tmp_path),
                "remote": "origin",
                "ref": "refs/heads/feature",
                "remote_oid": "c" * 40,
            },
        )
        _save_ci_effect(
            manager,
            effect_id="effect:github.open_pr:cancel",
            capability_ref="github.open_pr",
            receipt={
                "owner": "openminion",
                "repo": "example",
                "number": 17,
                "head": "feature",
                "base": "dev",
                "head_sha": "c" * 40,
            },
        )
        return ProjectTurnResult(summary="opened")

    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=open_pull_request,
        verify=lambda: (_evidence(_TestEvidenceStatus.PASSED),),
        fetch_checks=lambda _args: pytest.fail("cancelled project rechecked CI"),
    )
    opened = worker.run_cycle(run.run_id, triggering_cron_job_id="wake:0")
    manager.transition_task(
        task_id="task-1",
        to_state=TaskLifecycleState.CANCELLED,
    )
    cancelled = worker.run_cycle(
        run.run_id,
        triggering_cron_job_id=opened.project_run.next_wake_job_id,
    )
    manager.close()
    restarted = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    replay = ProjectWorker(
        task_manager=restarted,
        autonomy_store=store,
        turn=lambda _request: pytest.fail("cancel replay ran a model turn"),
        verify=lambda: pytest.fail("cancel replay ran verification"),
        fetch_checks=lambda _args: pytest.fail("cancel replay rechecked CI"),
    ).run_cycle(
        run.run_id,
        triggering_cron_job_id=opened.project_run.next_wake_job_id,
    )

    assert cancelled.run.status == AutonomyRunStatus.CANCELLED
    assert cancelled.check_events[-1]["overall_result"] == "cancelled"
    assert cancelled.project_run.next_wake_job_id is None
    assert replay.reconciled_only is True
    assert replay.check_events == ()
    checkpoint = load_latest_project_checkpoint(restarted, task_id="task-1")
    assert checkpoint is not None
    assert checkpoint.project_run.effect_refs == (
        "effect:git.push:cancel",
        "effect:github.open_pr:cancel",
    )


def test_expired_project_check_wait_does_not_read_or_schedule_again(tmp_path) -> None:
    store, manager, run = _project(tmp_path, expected_checks=("lint",))

    def open_pull_request(_request: ProjectTurnRequest) -> ProjectTurnResult:
        _save_ci_effect(
            manager,
            effect_id="effect:git.push:expiry",
            capability_ref="git.push",
            receipt={
                "repository": str(tmp_path),
                "remote": "origin",
                "ref": "refs/heads/feature",
                "remote_oid": "d" * 40,
            },
        )
        _save_ci_effect(
            manager,
            effect_id="effect:github.open_pr:expiry",
            capability_ref="github.open_pr",
            receipt={
                "owner": "openminion",
                "repo": "example",
                "number": 17,
                "head": "feature",
                "base": "dev",
                "head_sha": "d" * 40,
            },
        )
        return ProjectTurnResult(summary="opened")

    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=open_pull_request,
        verify=lambda: (_evidence(_TestEvidenceStatus.PASSED),),
        fetch_checks=lambda _args: pytest.fail("expired project rechecked CI"),
    )
    opened = worker.run_cycle(run.run_id, triggering_cron_job_id="wake:0")
    current = store.require(run.run_id)
    store.save(
        current.model_copy(
            update={
                "created_at_ms": 0,
                "continuation_policy": current.continuation_policy.model_copy(
                    update={"max_wall_clock_ms": 1}
                ),
            }
        )
    )

    expired = worker.run_cycle(
        run.run_id,
        triggering_cron_job_id=opened.project_run.next_wake_job_id,
    )
    manager.close()
    restarted = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    replay = ProjectWorker(
        task_manager=restarted,
        autonomy_store=store,
        turn=lambda _request: pytest.fail("expiry replay ran a model turn"),
        verify=lambda: pytest.fail("expiry replay ran verification"),
        fetch_checks=lambda _args: pytest.fail("expiry replay rechecked CI"),
    ).run_cycle(
        run.run_id,
        triggering_cron_job_id=opened.project_run.next_wake_job_id,
    )

    assert expired.decision == ProjectCycleDecision.BLOCKED
    assert expired.check_events[-1]["overall_result"] == "expired"
    assert expired.project_run.next_wake_job_id is None
    assert replay.reconciled_only is True
    assert replay.check_events == ()


def test_repository_project_requires_completed_public_task_plan(tmp_path) -> None:
    store, manager, run = _project(
        tmp_path,
        max_iterations=2,
        task_plan_required=True,
    )
    plan = TaskPlan(
        plan_id="plan-1",
        objective="Ship the fixture",
        steps=[{"step_id": "build", "description": "Build it"}],
    )
    turns = iter(
        (
            ProjectTurnResult(summary="planned", task_plan=plan),
            ProjectTurnResult(
                summary="completed",
                task_plan_step_completed=TaskPlanStepCompleted(
                    plan_id="plan-1",
                    step_id="build",
                    output_summary="built",
                ),
                task_plan_completed=TaskPlanTerminalSignal(plan_id="plan-1"),
            ),
        )
    )
    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=lambda _request: next(turns),
        verify=lambda: (_evidence(_TestEvidenceStatus.PASSED),),
        owner_id="worker-1",
    )

    first = worker.run_cycle(run.run_id)
    result = worker.run_cycle(run.run_id)
    checkpoint = load_latest_project_checkpoint(manager, task_id="task-1")

    assert first.decision == ProjectCycleDecision.CONTINUE
    assert first.project_run.current_milestone == "Build it"
    assert result.decision == ProjectCycleDecision.STOP
    assert result.run.status == AutonomyRunStatus.COMPLETED
    assert checkpoint is not None
    assert checkpoint.payload["task_plan"]["status"] == "completed"


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
