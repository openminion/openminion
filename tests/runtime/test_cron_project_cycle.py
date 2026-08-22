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
    save_project_run_checkpoint,
)
from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
from openminion.modules.task.scheduling.schedule import (
    normalize_payload,
    validate_target_payload_pair,
)
from openminion.services.runtime.cron.executor import CronTurnExecutor


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


def _seed_project(tmp_path, monkeypatch, *, verifier_passes: bool):
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
        payload={"decision": "continue", "replan_count": 0},
    )
    runtime_manager = _RuntimeManager()
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            runtime=SimpleNamespace(env=env),
            agents={"agent-main": SimpleNamespace(name="agent-main")},
            default_agent="agent-main",
        ),
        runtime_manager=runtime_manager,
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
        },
    }
    return executor, cron_store, runtime_manager, job


def test_project_cycle_schedules_one_deterministic_wake_and_reconciles_retry(
    tmp_path,
    monkeypatch,
) -> None:
    executor, cron_store, runtime_manager, job = _seed_project(
        tmp_path,
        monkeypatch,
        verifier_passes=False,
    )

    first = executor.execute(job, {"run_id": "cron-run-1"})
    replay = executor.execute(job, {"run_id": "cron-run-1-retry"})

    next_job_id = first["metadata"]["next_wake_job_id"]
    assert first["metadata"]["decision"] == "continue"
    assert next_job_id in cron_store.jobs
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
    executor, cron_store, runtime_manager, job = _seed_project(
        tmp_path,
        monkeypatch,
        verifier_passes=True,
    )

    result = executor.execute(job, {"run_id": "cron-run-1"})

    assert result["metadata"]["decision"] == "stop"
    assert result["metadata"]["next_wake_job_id"] is None
    assert cron_store.jobs == {}
    assert len(runtime_manager.submitted) == 1


def test_project_cycle_payload_is_isolated_and_requires_durable_ids() -> None:
    assert normalize_payload(
        {"kind": "projectCycle", "run_id": "run-1", "task_id": "task-1"}
    ) == {"kind": "projectCycle", "run_id": "run-1", "task_id": "task-1"}
    validate_target_payload_pair(
        session_target="isolated",
        payload_kind="projectCycle",
    )
