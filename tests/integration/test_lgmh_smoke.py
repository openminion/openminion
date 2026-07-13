from __future__ import annotations

from types import SimpleNamespace

from openminion.cli.commands.goal import execute_goal_cli_command
from openminion.modules.brain.checkpoint import CheckpointManager
from openminion.modules.brain.loop.tools.task_ops import stable_task_id_for_plan_id
from openminion.modules.brain.runtime.goal.long_running import LongRunningGoalRuntime
from openminion.modules.brain.schemas import (
    BudgetCounters,
    Deliverable,
    Goal,
    MissionBudgetEnvelope,
    MissionState,
    SuccessCriterion,
)
from openminion.modules.brain.storage.goals import SQLiteGoalStore
from openminion.modules.brain.storage.missions import SQLiteMissionStateStore
from openminion.services.runtime.cron.executor import CronTurnExecutor


class _FakeTaskService:
    def __init__(self) -> None:
        self.checkpoints: dict[str, tuple[str, dict[str, object]]] = {}

    def get_latest_checkpoint(self, task_id: str):
        return self.checkpoints.get(task_id)

    def save_checkpoint(
        self, task_id: str, checkpoint_id: str, payload: dict[str, object]
    ):
        self.checkpoints[task_id] = (checkpoint_id, dict(payload))


class _FakeSessionApi:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def append_event(
        self, session_id: str, event_type: str, payload: dict[str, object], **_: object
    ):
        self.events.append((event_type, dict(payload)))
        return SimpleNamespace(id=f"evt-{len(self.events)}", session_id=session_id)

    def list_events(self, session_id: str):  # noqa: ARG002
        return []


class _FakeHandle:
    def __init__(self, result: object) -> None:
        self._result = result

    def result(self, timeout_s: float = 0) -> object:  # noqa: ARG002
        return self._result


class _FakeRuntimeManager:
    def __init__(self) -> None:
        self.submitted: list[object] = []

    def submit_turn(self, request):
        self.submitted.append(request)
        return _FakeHandle(SimpleNamespace(final_text="cron ok", metadata={}))


class _FakeCronStore(_FakeSessionApi):
    def replace_cron_job_payload(self, job_id: str, payload: dict[str, object]) -> None:  # noqa: ARG002
        return None

    def delete_old_cron_runs(self, cutoff: str) -> int:  # noqa: ARG002
        return 0


def _request_builder(payload: dict[str, object], agent_id: str) -> object:
    return SimpleNamespace(
        agent_id=agent_id,
        session_id=str(payload.get("session_id") or ""),
        trace_id=str(payload.get("trace_id") or ""),
        meta=dict(payload.get("meta") or {}),
        payload=dict(payload),
    )


def _goal(goal_id: str, *, apd_plan_id: str, status: str) -> Goal:
    return Goal(
        goal_id=goal_id,
        description=f"goal {goal_id}",
        status=status,
        apd_plan_id=apd_plan_id,
        success_criteria=[
            SuccessCriterion(
                criterion_id="criterion-1",
                description="criterion",
                structural_check="artifact_present",
            )
        ],
        deliverables=[
            Deliverable(
                deliverable_id="deliverable-1",
                description="deliverable",
            )
        ],
    )


def _mission(mission_id: str, *, task_id: str, status: str) -> MissionState:
    counters = BudgetCounters(
        ticks=5, tool_calls=5, a2a_calls=0, tokens=500, time_ms=1000
    )
    return MissionState(
        mission_id=mission_id,
        objective="mission",
        task_id=task_id,
        status=status,
        budget=MissionBudgetEnvelope(
            total_remaining=counters,
            per_turn_max=counters,
            remaining_llm_calls_total=5,
            llm_calls_per_turn_max=1,
        ),
    )


def test_lgmh_smoke_cross_session_cron_and_goal_cli(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "brain.db"
    plan_id = "plan-smoke"
    task_id = stable_task_id_for_plan_id(plan_id)
    goal_store = SQLiteGoalStore(db_path)
    mission_store = SQLiteMissionStateStore(db_path)
    goal_store.create(_goal("goal-smoke", apd_plan_id=plan_id, status="paused"))
    goal_store.bind_to_session("goal-smoke", "sess-smoke")
    mission_store.create(_mission("mission-smoke", task_id=task_id, status="paused"))

    task_service = _FakeTaskService()
    checkpoint_manager = CheckpointManager(task_service=task_service)
    checkpoint_manager.save_payload(
        owner="coding",
        version=1,
        task_id=task_id,
        payload={"cursor": 3},
    )
    runtime = LongRunningGoalRuntime(
        goal_store=goal_store,
        mission_store=mission_store,
        checkpoint_manager=checkpoint_manager,
    )

    session_api = _FakeSessionApi()
    snapshots = runtime.hydrate_session_start(
        session_id="sess-smoke",
        session_api=session_api,
    )
    assert snapshots[0].checkpoint is not None
    assert snapshots[0].mission_id == "mission-smoke"

    cron_store = _FakeCronStore()
    runtime_manager = _FakeRuntimeManager()
    agent_service = SimpleNamespace(_runner=SimpleNamespace(goal_runtime=runtime))
    executor = CronTurnExecutor(
        runtime=SimpleNamespace(
            config=SimpleNamespace(
                agent=SimpleNamespace(name="agent-smoke"),
                agents={"agent-smoke": SimpleNamespace(name="agent-smoke")},
                default_agent="agent-smoke",
            ),
            runtime_manager=runtime_manager,
            list_registered_agents=lambda: ["agent-smoke"],
            resolve_agent_service=lambda _agent_id: agent_service,
        ),
        cron_store=cron_store,
        request_builder=_request_builder,
        timeout_s=30.0,
        max_attempts=1,
    )

    result = executor.execute(
        {
            "job_id": "job-smoke",
            "agent_id": "agent-smoke",
            "payload": {
                "kind": "agentTurn",
                "message": "resume goal",
                "goal_id": "goal-smoke",
                "mission_id": "mission-smoke",
            },
        },
        {
            "run_id": "run-smoke",
            "due_at": "2026-05-24T00:00:00Z",
            "isolated_session_id": "cron-session-smoke",
        },
    )
    assert result["summary"] == "cron ok"
    assert runtime_manager.submitted[0].meta["goal_context_preloaded"] == "true"
    assert goal_store.get("goal-smoke").status == "active"  # type: ignore[union-attr]
    assert mission_store.get("mission-smoke").status == "active"  # type: ignore[union-attr]

    del monkeypatch
    _tone, rendered = execute_goal_cli_command(
        "/goal show goal-smoke",
        session_id="sess-smoke",
        db_path=db_path,
    )
    assert "goal-smoke [active] goal goal-smoke" in rendered
    assert "success_criteria=1" in rendered
