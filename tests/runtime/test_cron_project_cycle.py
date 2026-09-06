from __future__ import annotations

import json
import shlex
import sys
from types import SimpleNamespace

from openminion.modules.storage.runtime.sqlite import resolve_database_path
from openminion.modules.task import (
    AutonomyRunPhase,
    AutonomyRunStatus,
    AutonomyRunStore,
    TaskLifecycleRepository,
    TaskManager,
    build_autonomy_run,
    build_project_run_projection,
    load_latest_project_checkpoint,
    save_project_run_checkpoint,
)
from openminion.modules.task.project import checkpoints as project_checkpoints
from openminion.modules.task.project.effects import (
    ProjectEffectRecord,
    ProjectEffectStatus,
    save_project_effect_record,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
from openminion.modules.task.scheduling.schedule import (
    normalize_payload,
    validate_target_payload_pair,
)
from openminion.services.runtime.cron.executor import CronTurnExecutor
from openminion.tools.github import plugin as github_plugin
from openminion.tools.github.constants import DEFAULT_GITHUB_PROVIDER_ID
from openminion.tools.github.providers import provider_registry


class _Handle:
    def __init__(self, result: object) -> None:
        self._result = result

    def result(self, timeout_s: float = 0) -> object:  # noqa: ARG002
        return self._result


class _RuntimeManager:
    def __init__(self) -> None:
        self.submitted: list[object] = []

    def submit_turn(self, request: object) -> _Handle:
        self.submitted.append(request)
        return _Handle(SimpleNamespace(final_text="worked", metadata={}))


class _Sessions:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def append_event(self, **kwargs: object) -> None:
        self.events.append(dict(kwargs))


class _Telemetry:
    def __init__(self) -> None:
        self.operations: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def emit_module_operation(self, *args: object, **kwargs: object) -> None:
        self.operations.append((args, kwargs))


class _ChecksProvider:
    provider_id = DEFAULT_GITHUB_PROVIDER_ID

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def fetch_checks(self, *, args, ctx):  # noqa: ANN001
        del ctx
        self.calls.append(dict(args))
        return {
            "ok": True,
            "data": {
                "head_sha": args["head_sha"],
                "overall_result": "pending",
                "expected_checks": list(args["expected_checks"]),
                "missing_expected_checks": ["tests (3.11)"],
                "failure_facts": [],
            },
            "source": {"provider_id": self.provider_id},
        }


class _CronStore:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, object]] = {}

    def get_cron_job(self, job_id: str) -> dict[str, object] | None:
        return self.jobs.get(job_id)

    def add_cron_job(self, *, job_id: str | None = None, **kwargs: object) -> str:
        assert job_id is not None
        self.jobs[job_id] = {"job_id": job_id, **kwargs}
        return job_id

    def delete_cron_job(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)


def _request_builder(payload: dict[str, object], agent_id: str) -> object:
    return SimpleNamespace(
        agent_id=agent_id,
        session_id=str(payload.get("session_id") or ""),
        trace_id=str(payload.get("trace_id") or ""),
        meta=dict(payload.get("meta") or {}),
        payload=payload,
    )


def _seed_project(
    tmp_path,
    monkeypatch,
    *,
    verifier_passes: bool,
    expected_checks: tuple[str, ...] = (),
):
    home = tmp_path / "home"
    data = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_HOME", str(home))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data))
    monkeypatch.setenv("OPENMINION_GENERATED_ROOT", str(data / "runtime"))
    env = {
        "OPENMINION_HOME": str(home),
        "OPENMINION_DATA_ROOT": str(data),
    }
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        "raise SystemExit(0)" if verifier_passes else "raise SystemExit(1)"
    )
    autonomy_store = AutonomyRunStore()
    run = build_autonomy_run(
        goal_text="Finish the scheduled project",
        goal_id="goal-1",
        session_id="session-1",
        workspace_ref=f"local:{tmp_path}#commit=abc;dirty=clean",
        max_iterations=3,
        agent_id="agent-main",
        verification_domain="coding",
        verification_commands=(command,),
    ).model_copy(
        update={
            "task_id": "task-1",
            "status": AutonomyRunStatus.RUNNING,
            "phase": AutonomyRunPhase.EXECUTE,
        }
    )
    autonomy_store.create(run)
    task_db = resolve_database_path(DEFAULT_INTEGRATED_SQLITE_SUBPATH, env=env)
    cron_store = _CronStore()
    task_manager = TaskManager(
        cron_repository=cron_store,
        lifecycle_repository=TaskLifecycleRepository(db_path=task_db),
    )
    task_manager.create_task(
        session_id=run.session_id,
        mode_name="project",
        goal=run.goal_text,
        agent_id="agent-main",
        task_id="task-1",
    )
    project = build_project_run_projection(
        run,
        objective_ledger_ref="project:objective",
        evidence_ledger_ref="project:evidence",
        resume_packet_ref="project:resume",
        operator_decision_log_ref="project:operator",
        capability_plan_ref="project:capabilities",
        metrics_summary_ref="project:metrics",
    )
    save_project_run_checkpoint(
        task_manager,
        project,
        checkpoint_id="initial",
        payload={
            "decision": "continue",
            "replan_count": 0,
            **project_checkpoints.initial_repository_lifecycle_payload(
                run,
                project,
                expected_checks=expected_checks,
            ),
        },
    )
    if expected_checks:
        push = ProjectEffectRecord(
            effect_id="effect:git.push:cron",
            task_id="task-1",
            idempotency_key="push-cron",
            actor_ref="agent:agent-main",
            capability_ref="git.push",
            precondition_refs=("git:head:" + "a" * 40,),
            result_ref="git:remote:origin:refs/heads/feature@" + "a" * 40,
            non_reversible_reason="The remote branch remains published.",
            status=ProjectEffectStatus.SUCCEEDED,
        )
        save_project_effect_record(
            task_manager,
            push,
            receipt={
                "repository": str(tmp_path),
                "remote": "origin",
                "ref": "refs/heads/feature",
                "remote_oid": "a" * 40,
            },
        )
        effect = ProjectEffectRecord(
            effect_id="effect:github.open_pr:cron",
            task_id="task-1",
            idempotency_key="open-pr-cron",
            actor_ref="agent:agent-main",
            capability_ref="github.open_pr",
            precondition_refs=("github:head:" + "a" * 40,),
            result_ref="github:pull:openminion/example#17",
            non_reversible_reason="The pull request remains open.",
            status=ProjectEffectStatus.SUCCEEDED,
        )
        save_project_effect_record(
            task_manager,
            effect,
            receipt={
                "owner": "openminion",
                "repo": "example",
                "number": 17,
                "head": "feature",
                "base": "dev",
                "head_sha": "a" * 40,
            },
        )
        checkpoint = load_latest_project_checkpoint(task_manager, task_id="task-1")
        assert checkpoint is not None
        checkpoint, started = project_checkpoints.begin_repository_check_observation(
            checkpoint
        )
        assert started is True
        task_manager.save_checkpoint(
            "task-1",
            checkpoint.checkpoint_id,
            checkpoint.model_dump(mode="json"),
        )
    runtime_manager = _RuntimeManager()
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            runtime=SimpleNamespace(env=env),
            agents={"agent-main": SimpleNamespace(name="agent-main")},
            default_agent="agent-main",
        ),
        runtime_manager=runtime_manager,
        tools=ToolRegistry(),
        sessions=_Sessions(),
        telemetry_service=_Telemetry(),
        list_registered_agents=lambda: ["agent-main"],
        resolve_agent_service=lambda _agent_id: SimpleNamespace(_runner=None),
    )
    executor = CronTurnExecutor(
        runtime=runtime,
        cron_store=cron_store,
        request_builder=_request_builder,
        timeout_s=10,
        max_attempts=1,
    )
    job = {
        "job_id": "prun_1:wake:0",
        "agent_id": "agent-main",
        "payload": {
            "kind": "projectCycle",
            "run_id": run.run_id,
            "task_id": run.task_id,
            "goal_id": run.goal_id,
            "session_id": run.session_id,
            "cycle_interval_seconds": 17,
        },
    }
    return executor, cron_store, runtime_manager, job, runtime


def test_project_cycle_schedules_one_deterministic_wake_and_reconciles_retry(
    tmp_path,
    monkeypatch,
) -> None:
    executor, cron_store, runtime_manager, job, _runtime = _seed_project(
        tmp_path,
        monkeypatch,
        verifier_passes=False,
    )

    first = executor.execute(job, {"run_id": "cron-run-1"})
    replay = executor.execute(job, {"run_id": "cron-run-1-retry"})

    next_job_id = first["metadata"]["next_wake_job_id"]
    assert first["metadata"]["decision"] == "continue"
    assert next_job_id in cron_store.jobs
    assert cron_store.jobs[next_job_id]["payload"]["cycle_interval_seconds"] == 17
    assert replay["metadata"]["reconciled_only"] is True
    assert replay["metadata"]["next_wake_job_id"] == next_job_id
    assert len(runtime_manager.submitted) == 1
    assert runtime_manager.submitted[0].payload["meta"]["inbound_metadata"][
        "workspace_root"
    ] == str(tmp_path)
    assert (
        runtime_manager.submitted[0].payload["meta"]["inbound_metadata"][
            "caller_handles_delivery"
        ]
        == "true"
    )
    assert (
        runtime_manager.submitted[0]
        .payload["meta"]["inbound_metadata"]["conversation_id"]
        .startswith("prun_")
    )
    assert (
        runtime_manager.submitted[0].payload["meta"]["inbound_metadata"]["resume"]
        == "true"
    )
    assert runtime_manager.submitted[0].payload["timeout_seconds"] == 300
    assert (
        runtime_manager.submitted[0].payload["meta"]["inbound_metadata"][
            "turn_timeout_seconds"
        ]
        == "300"
    )
    assert json.loads(
        runtime_manager.submitted[0].payload["meta"]["inbound_metadata"][
            "permission_overrides"
        ]
    ) == {
        "file.copy": "bypass",
        "file.move": "bypass",
        "file.write": "bypass",
    }


def test_verified_project_cycle_finishes_without_another_wake(
    tmp_path,
    monkeypatch,
) -> None:
    executor, cron_store, runtime_manager, job, _runtime = _seed_project(
        tmp_path,
        monkeypatch,
        verifier_passes=True,
    )

    result = executor.execute(job, {"run_id": "cron-run-1"})

    assert result["metadata"]["decision"] == "stop"
    assert result["metadata"]["next_wake_job_id"] is None
    assert cron_store.jobs == {}
    assert len(runtime_manager.submitted) == 1


def test_scheduled_project_check_uses_tool_owner_and_records_waiting_facts(
    tmp_path,
    monkeypatch,
) -> None:
    expected_checks = ("lint", "tests (3.11)")
    executor, cron_store, runtime_manager, job, runtime = _seed_project(
        tmp_path,
        monkeypatch,
        verifier_passes=True,
        expected_checks=expected_checks,
    )
    provider = _ChecksProvider()
    github_plugin.register(runtime.tools)
    provider_registry().register(provider)
    try:
        first = executor.execute(job, {"run_id": "cron-run-1"})
        replay = executor.execute(job, {"run_id": "cron-run-1-retry"})
    finally:
        provider_registry().reset()

    next_job_id = first["metadata"]["next_wake_job_id"]
    assert next_job_id in cron_store.jobs
    assert first["metadata"]["detail_code"] == "waiting_for_checks"
    assert first["metadata"]["check_events"][0]["head_sha"] == "a" * 40
    assert replay["metadata"]["reconciled_only"] is True
    assert provider.calls == [
        {
            "owner": "openminion",
            "repo": "example",
            "head_sha": "a" * 40,
            "expected_checks": list(expected_checks),
        }
    ]
    assert runtime_manager.submitted == []
    assert runtime.sessions.events[0]["event_type"] == "project.checks.pending"
    event_payload = runtime.sessions.events[0]["payload"]
    assert event_payload["project_run_id"].startswith("prun_")
    assert event_payload["expected_checks"] == list(expected_checks)
    assert event_payload["wait_duration_ms"] >= 0
    assert runtime.telemetry_service.operations[0][0][3] == "project_checks"
    assert runtime.telemetry_service.operations[0][1]["extra"]["wait_duration_ms"] >= 0


def test_project_cycle_payload_is_isolated_and_requires_durable_ids() -> None:
    assert normalize_payload(
        {
            "kind": "projectCycle",
            "run_id": "run-1",
            "task_id": "task-1",
            "cycle_interval_seconds": 17,
        }
    ) == {
        "kind": "projectCycle",
        "run_id": "run-1",
        "task_id": "task-1",
        "cycle_interval_seconds": 17,
    }
    validate_target_payload_pair(
        session_target="isolated",
        payload_kind="projectCycle",
    )
