from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.checkpoint import CheckpointManager
from openminion.modules.brain.runtime.goal.long_running import (
    LongRunningGoalRuntime,
    render_goal_summary,
    render_goal_verification,
)
from openminion.modules.brain.schemas import (
    WorkingState,
    BudgetCounters,
    Deliverable,
    ExternalBlocker,
    Goal,
    MissionBudgetEnvelope,
    MissionState,
    SuccessCriterion,
)
from openminion.modules.brain.storage.goals import SQLiteGoalStore
from openminion.modules.brain.storage.missions import SQLiteMissionStateStore
from openminion.modules.brain.loop.tools.task_ops import stable_task_id_for_plan_id


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


def _goal(goal_id: str, **overrides) -> Goal:
    return Goal(
        goal_id=goal_id,
        description="goal",
        success_criteria=[
            SuccessCriterion(
                criterion_id="criterion-1",
                description="criterion",
                structural_check="success_criteria.ok=true",
            )
        ],
        deliverables=[
            Deliverable(
                deliverable_id="deliverable-1",
                description="deliverable",
            )
        ],
        **overrides,
    )


def _mission(mission_id: str, *, task_id: str) -> MissionState:
    counters = BudgetCounters(
        ticks=10, tool_calls=10, a2a_calls=0, tokens=1000, time_ms=60000
    )
    return MissionState(
        mission_id=mission_id,
        objective="mission",
        task_id=task_id,
        budget=MissionBudgetEnvelope(
            total_remaining=counters,
            per_turn_max=counters,
            remaining_llm_calls_total=10,
            llm_calls_per_turn_max=2,
        ),
    )


def _state(session_id: str) -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="agent-1",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=10,
            a2a_calls=0,
            tokens=1000,
            time_ms=60000,
        ),
    )


def test_goal_runtime_hydrates_resume_context_with_checkpoint(tmp_path) -> None:
    db_path = tmp_path / "brain.db"
    goal_store = SQLiteGoalStore(db_path)
    mission_store = SQLiteMissionStateStore(db_path)
    goal_store.create(_goal("goal-1", apd_plan_id="plan-1"))
    task_id = stable_task_id_for_plan_id("plan-1")
    mission_store.create(_mission("mission-1", task_id=task_id))
    task_service = _FakeTaskService()
    manager = CheckpointManager(task_service=task_service)
    manager.save_payload(
        owner="coding",
        version=1,
        task_id=task_id,
        payload={"cursor": 2},
    )
    session_api = _FakeSessionApi()

    runtime = LongRunningGoalRuntime(
        goal_store=goal_store,
        mission_store=mission_store,
        checkpoint_manager=manager,
    )
    snapshots = runtime.hydrate_session_start(
        session_id="sess-1", session_api=session_api
    )

    assert len(snapshots) == 1
    assert snapshots[0].mission_id == "mission-1"
    assert snapshots[0].checkpoint is not None
    assert session_api.events[0][0] == "goal.resume_context.loaded"


def test_goal_runtime_advances_from_cron_and_respects_blockers(tmp_path) -> None:
    db_path = tmp_path / "brain.db"
    goal_store = SQLiteGoalStore(db_path)
    mission_store = SQLiteMissionStateStore(db_path)
    blocked_goal = goal_store.create(
        _goal(
            "goal-blocked",
            apd_plan_id="plan-1",
            status="paused",
            external_blockers=[
                ExternalBlocker(
                    blocker_id="blk-1",
                    kind="human_approval",
                    descriptor="wait",
                    created_at="2026-05-24T00:00:00Z",
                )
            ],
        )
    )
    resumable_goal = goal_store.create(_goal("goal-open", status="paused"))
    mission_store.create(_mission("mission-1", task_id="task-1"))
    mission_store.pause("mission-1", reason="wait")

    runtime = LongRunningGoalRuntime(
        goal_store=goal_store,
        mission_store=mission_store,
    )
    runtime.advance_from_cron(goal_id=blocked_goal.goal_id, mission_id="mission-1")
    runtime.advance_from_cron(goal_id=resumable_goal.goal_id, mission_id=None)

    assert goal_store.get("goal-blocked").status == "paused"  # type: ignore[union-attr]
    assert goal_store.get("goal-open").status == "active"  # type: ignore[union-attr]
    assert mission_store.get("mission-1").status == "active"  # type: ignore[union-attr]


def test_goal_runtime_applies_task_plan_terminal_signals_and_cost_budget(
    tmp_path,
) -> None:
    db_path = tmp_path / "brain.db"
    goal_store = SQLiteGoalStore(db_path)
    mission_store = SQLiteMissionStateStore(db_path)
    goal_store.create(_goal("goal-plan", apd_plan_id="plan-1"))
    goal_store.create(_goal("goal-cost", cost_budget_tokens=100))

    runtime = LongRunningGoalRuntime(
        goal_store=goal_store,
        mission_store=mission_store,
    )
    runtime.apply_task_plan_signal(
        plan_id="plan-1",
        root_goal_id="goal-plan",
        terminal_status="completed",
        reason="task_plan_completed",
    )
    runtime.consume_cost(goal_id="goal-cost", consumed_tokens=101)

    assert goal_store.get("goal-plan").status == "completed"  # type: ignore[union-attr]
    assert goal_store.get("goal-cost").status == "halted"  # type: ignore[union-attr]
    assert goal_store.get("goal-cost").failure_conditions[-1].kind == "budget_exhausted"  # type: ignore[union-attr]


def test_goal_runtime_can_resolve_goal_from_plan_id_without_explicit_root_goal_id(
    tmp_path,
) -> None:
    db_path = tmp_path / "brain.db"
    goal_store = SQLiteGoalStore(db_path)
    mission_store = SQLiteMissionStateStore(db_path)
    goal_store.create(_goal("goal-plan-fallback", apd_plan_id="plan-fallback"))

    runtime = LongRunningGoalRuntime(
        goal_store=goal_store,
        mission_store=mission_store,
    )
    updated = runtime.apply_task_plan_signal(
        plan_id="plan-fallback",
        root_goal_id=None,
        terminal_status="completed",
        reason="task_plan_completed",
    )

    assert updated is not None
    assert updated.goal_id == "goal-plan-fallback"
    assert updated.status == "completed"


def test_goal_runtime_records_goal_render_helpers_and_audit_rows(tmp_path) -> None:
    db_path = tmp_path / "brain.db"
    goal_store = SQLiteGoalStore(db_path)
    mission_store = SQLiteMissionStateStore(db_path)
    goal = goal_store.create(_goal("goal-audit", cost_budget_dollars=1.5))

    runtime = LongRunningGoalRuntime(
        goal_store=goal_store,
        mission_store=mission_store,
    )
    exhausted = runtime.consume_cost(goal_id=goal.goal_id, consumed_dollars=2.0)

    assert exhausted is not None
    assert "blockers=0" not in render_goal_summary(exhausted)
    audit = goal_store.list_goal_audit_trail(goal.goal_id)
    assert any(row.reason == "cost_budget" for row in audit)
    rendered = render_goal_verification(
        goal.goal_id,
        runtime.verify_goal_for_cli(
            goal_id=goal.goal_id,
            run_id="goal-cli-audit",
            state=WorkingState(
                session_id="sess-render",
                agent_id="cli",
                budgets_remaining=BudgetCounters(
                    ticks=1,
                    tool_calls=1,
                    a2a_calls=0,
                    tokens=1,
                    time_ms=1,
                ),
                trace_id="goal-cli-audit",
            ),
            logger=SimpleNamespace(emit=lambda **_: None),
        ),
    )
    assert "goal=goal-audit" in rendered
    assert "status=incomplete" in rendered
