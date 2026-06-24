from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.loop.tools.plan_control import (
    _set_active_plan_override,
    _sync_goal_plan_declare,
    _sync_goal_plan_step,
    build_plan_tool_spec,
)
from openminion.modules.context.schemas import TaskPlan, TaskPlanStep


class _FakeGoalRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | None]] = []

    def apply_task_plan_signal(
        self,
        *,
        plan_id: str,
        root_goal_id: str | None,
        terminal_status: str,
        reason: str,
    ) -> None:
        self.calls.append(
            {
                "plan_id": plan_id,
                "root_goal_id": root_goal_id,
                "terminal_status": terminal_status,
                "reason": reason,
            }
        )


def test_build_plan_tool_spec_exposes_root_goal_id() -> None:
    spec = build_plan_tool_spec()
    properties = dict(spec.input_schema.get("properties") or {})

    assert "root_goal_id" in properties
    assert properties["root_goal_id"]["type"] == "string"


def test_goal_plan_sync_for_declare_and_terminal_step_uses_root_goal_id() -> None:
    goal_runtime = _FakeGoalRuntime()
    loop_ctx = SimpleNamespace(_runner=SimpleNamespace(goal_runtime=goal_runtime))
    plan = TaskPlan(
        plan_id="plan-1",
        objective="ship the change",
        root_goal_id="goal-1",
        status="active",
        steps=[
            TaskPlanStep(
                step_id="step-1",
                description="do work",
                status="pending",
            )
        ],
    )

    _sync_goal_plan_declare(loop_ctx, plan=plan)
    _set_active_plan_override(loop_ctx, plan.model_dump(mode="json"))
    _sync_goal_plan_step(
        loop_ctx,
        plan_id="plan-1",
        terminal_status="completed",
    )

    assert goal_runtime.calls == [
        {
            "plan_id": "plan-1",
            "root_goal_id": "goal-1",
            "terminal_status": "active",
            "reason": "task_plan_declared",
        },
        {
            "plan_id": "plan-1",
            "root_goal_id": "goal-1",
            "terminal_status": "completed",
            "reason": "task_plan_completed",
        },
    ]
