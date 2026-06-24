from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openminion.modules.brain.loop.tools.plan_control import (
    build_plan_tool_spec,
    handle_plan_tool_call,
)
from openminion.modules.brain.loop.tools.task_ops import (
    PLAN_TASK_OPS_OUTPUT_KEY,
    PLAN_TASK_OPS_TOUCHED_TASK_IDS_OUTPUT_KEY,
    stable_task_id_for_plan_id,
)
from openminion.modules.brain.schemas import BudgetCounters, WorkingState
from openminion.modules.task.runtime.service import InMemoryTaskCtl
from openminion.modules.task.schemas import PlanStepStatus


def _active_plan() -> dict[str, Any]:
    return {
        "plan_id": "plan-1",
        "objective": "Research and summarize",
        "status": "active",
        "steps": [
            {
                "step_id": "entry",
                "description": "Research entry requirements",
                "status": "pending",
                "estimated_difficulty": "low",
                "depends_on": [],
                "tool_families": ["web", "search"],
            },
            {
                "step_id": "transport",
                "description": "Research transport",
                "status": "pending",
                "estimated_difficulty": "low",
                "depends_on": ["entry"],
                "tool_families": ["web", "search"],
            },
        ],
    }


@dataclass
class _FakeSessionAPI:
    active_plan: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        self.events.append(
            {
                "session_id": session_id,
                "event_type": event_type,
                "payload": payload,
                "kwargs": dict(kwargs),
            }
        )

    def get_active_task_plan(self, session_id: str) -> dict[str, Any] | None:
        del session_id
        return dict(self.active_plan) if isinstance(self.active_plan, dict) else None


@dataclass
class _FakeSkillAPI:
    workflows: set[str] = field(default_factory=set)

    def get_workflow(self, workflow_id: str, **_: Any) -> dict[str, Any]:
        if workflow_id not in self.workflows:
            raise LookupError(workflow_id)
        return {"workflow_id": workflow_id}


@dataclass
class _Ctx:
    session_api: _FakeSessionAPI
    task_ctl: object | None = None
    skill_api: object | None = None
    state: WorkingState = field(
        default_factory=lambda: WorkingState(
            session_id="s-plan",
            agent_id="agent",
            trace_id="trace",
            budgets_remaining=BudgetCounters(
                ticks=10,
                tool_calls=5,
                a2a_calls=0,
                tokens=5000,
                time_ms=120000,
            ),
        )
    )


def test_plan_tool_spec_advertises_bounded_step_schema() -> None:
    spec = build_plan_tool_spec()

    step_schema = spec.input_schema["properties"]["steps"]["items"]
    tool_family_enum = step_schema["properties"]["tool_families"]["items"]["enum"]

    assert step_schema["required"] == ["step_id", "description"]
    assert step_schema["properties"]["estimated_difficulty"]["enum"] == [
        "low",
        "medium",
        "high",
    ]
    assert step_schema["properties"]["status"]["enum"] == [
        "pending",
        "in_progress",
        "completed",
        "blocked",
    ]
    assert "web" in tool_family_enum
    assert "search" in tool_family_enum
    assert "web_search" not in tool_family_enum
    assert tool_family_enum == sorted(tool_family_enum)
    assert spec.input_schema["properties"]["revised_steps"]["items"] == step_schema
    assert "workflow_id" in spec.input_schema["properties"]


def test_plan_control_declare_records_task_plan_event() -> None:
    session_api = _FakeSessionAPI()
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(
            session_api=session_api,
            skill_api=_FakeSkillAPI(workflows={"workflow.skill.research"}),
        ),
        arguments={
            "action": "declare",
            "plan_id": "plan-1",
            "objective": "Research and summarize",
            "workflow_id": "workflow.skill.research",
            "steps": _active_plan()["steps"],
        },
    )

    assert result.status == "success"
    assert [event["event_type"] for event in session_api.events] == [
        "task_plan.declared"
    ]
    assert session_api.events[0]["payload"]["plan"]["plan_id"] == "plan-1"
    assert (
        session_api.events[0]["payload"]["plan"]["workflow_id"]
        == "workflow.skill.research"
    )
    assert session_api.events[0]["kwargs"]["actor_type"] == "agent"


def test_plan_control_declare_accepts_stringified_steps_and_boolean() -> None:
    session_api = _FakeSessionAPI()
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api),
        arguments={
            "action": "declare",
            "plan_id": "plan-1",
            "objective": "Research and summarize",
            "steps": (
                '[{"step_id":"entry","description":"Research entry requirements",'
                '"tool_families":["web","search"]}]'
            ),
            "continue_plan_autonomously": "true",
        },
    )

    assert result.status == "success"
    assert session_api.events[0]["event_type"] == "task_plan.declared"
    assert session_api.events[0]["payload"]["plan"]["steps"][0]["step_id"] == "entry"
    assert result.outputs["plan.continue_plan_autonomously"] is True


def test_plan_control_declare_falls_back_to_plan_id_when_objective_missing() -> None:
    session_api = _FakeSessionAPI()
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api),
        arguments={
            "action": "declare",
            "plan_id": "cross-turn-goal-persistence",
            "steps": [
                {
                    "step_id": "step-1",
                    "description": "acknowledge start",
                }
            ],
            "continue_plan_autonomously": True,
        },
    )

    assert result.status == "success"
    assert session_api.events[0]["event_type"] == "task_plan.declared"
    assert (
        session_api.events[0]["payload"]["plan"]["objective"]
        == "cross-turn-goal-persistence"
    )
    assert result.outputs["plan.continue_plan_autonomously"] is True


def test_plan_control_rejects_unknown_workflow_id() -> None:
    session_api = _FakeSessionAPI()
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(
            session_api=session_api,
            skill_api=_FakeSkillAPI(workflows={"workflow.skill.research"}),
        ),
        arguments={
            "action": "declare",
            "plan_id": "plan-1",
            "objective": "Research and summarize",
            "workflow_id": "workflow.skill.missing",
            "steps": _active_plan()["steps"],
        },
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "PLAN_WORKFLOW_NOT_FOUND"


def test_plan_control_revision_preserves_active_workflow_id() -> None:
    session_api = _FakeSessionAPI(
        active_plan={
            **_active_plan(),
            "workflow_id": "workflow.skill.research",
        }
    )
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(
            session_api=session_api,
            skill_api=_FakeSkillAPI(workflows={"workflow.skill.research"}),
        ),
        arguments={
            "action": "revise",
            "plan_id": "plan-1",
            "reason": "Transport is no longer needed.",
            "revised_steps": [
                {
                    **_active_plan()["steps"][0],
                    "status": "completed",
                    "output_summary": "Entry rules found.",
                }
            ],
        },
    )

    assert result.status == "success"
    assert (
        session_api.events[0]["payload"]["plan"]["workflow_id"]
        == "workflow.skill.research"
    )


def test_plan_control_declare_maps_to_durable_task_ops_when_ctl_present() -> None:
    session_api = _FakeSessionAPI()
    task_ctl = InMemoryTaskCtl()

    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api, task_ctl=task_ctl),
        arguments={
            "action": "declare",
            "plan_id": "plan-1",
            "objective": "Research and summarize",
            "steps": _active_plan()["steps"],
        },
    )

    task_id = stable_task_id_for_plan_id("plan-1")
    task = task_ctl.get_task(task_id)
    assert result.status == "success"
    assert result.outputs[PLAN_TASK_OPS_OUTPUT_KEY]["ops"][0]["op"] == "task.create"
    assert result.outputs[PLAN_TASK_OPS_OUTPUT_KEY]["ops"][1]["op"] == (
        "task.attach_plan"
    )
    assert result.outputs[PLAN_TASK_OPS_TOUCHED_TASK_IDS_OUTPUT_KEY] == [
        task_id,
        task_id,
    ]
    assert task.current_plan_id == "plan-1"
    assert task.next_step_id == "entry"


def test_plan_control_step_completed_accepts_stringified_boolean() -> None:
    session_api = _FakeSessionAPI(active_plan=_active_plan())
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api),
        arguments={
            "action": "step_completed",
            "plan_id": "plan-1",
            "step_id": "entry",
            "outcome": "success",
            "continue_plan_autonomously": "true",
        },
    )

    assert result.status == "success"
    assert result.outputs["plan.continue_plan_autonomously"] is True


def test_plan_control_declare_task_ops_are_deterministic_without_ctl() -> None:
    arguments = {
        "action": "declare",
        "plan_id": "plan-1",
        "objective": "Research and summarize",
        "steps": _active_plan()["steps"],
    }

    first = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=_FakeSessionAPI()),
        arguments=arguments,
    )
    second = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=_FakeSessionAPI()),
        arguments=arguments,
    )

    assert first.status == "success"
    assert (
        first.outputs[PLAN_TASK_OPS_OUTPUT_KEY]
        == second.outputs[PLAN_TASK_OPS_OUTPUT_KEY]
    )


def test_plan_control_declare_replaces_prior_active_plan() -> None:
    session_api = _FakeSessionAPI(active_plan={**_active_plan(), "plan_id": "old-plan"})
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api),
        arguments={
            "action": "declare",
            "plan_id": "plan-1",
            "objective": "Research and summarize",
            "steps": _active_plan()["steps"],
        },
    )

    assert result.status == "success"
    assert [event["event_type"] for event in session_api.events] == [
        "task_plan.abandoned",
        "task_plan.declared",
    ]
    assert session_api.events[0]["payload"]["plan_id"] == "old-plan"


def test_plan_control_step_completed_records_active_step() -> None:
    session_api = _FakeSessionAPI(active_plan=_active_plan())
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api),
        arguments={
            "action": "step_completed",
            "plan_id": "plan-1",
            "step_id": "entry",
            "outcome": "success",
            "output_summary": "Entry rules found.",
        },
    )

    assert result.status == "success"
    assert session_api.events[0]["event_type"] == "task_plan.step_completed"
    assert session_api.events[0]["payload"]["step_id"] == "entry"


def test_plan_control_step_completed_maps_to_durable_step_update() -> None:
    session_api = _FakeSessionAPI(active_plan=_active_plan())
    task_ctl = InMemoryTaskCtl()
    task_id = stable_task_id_for_plan_id("plan-1")
    setup = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=_FakeSessionAPI(), task_ctl=task_ctl),
        arguments={
            "action": "declare",
            "plan_id": "plan-1",
            "objective": "Research and summarize",
            "steps": _active_plan()["steps"],
        },
    )
    assert setup.status == "success"

    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api, task_ctl=task_ctl),
        arguments={
            "action": "step_completed",
            "plan_id": "plan-1",
            "step_id": "entry",
            "outcome": "success",
            "output_summary": "Entry rules found.",
        },
    )

    assert result.status == "success"
    assert result.outputs[PLAN_TASK_OPS_OUTPUT_KEY]["ops"][0]["op"] == (
        "task.step_update"
    )
    task = task_ctl.get_task(task_id)
    plan = task_ctl._plans["plan-1"]  # noqa: SLF001 - white-box task wiring proof
    assert task.next_step_id == "transport"
    assert any(
        step.step_id == "entry" and step.status == PlanStepStatus.DONE
        for step in plan.steps
    )


def test_plan_control_step_blocked_records_active_step() -> None:
    session_api = _FakeSessionAPI(active_plan=_active_plan())
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api),
        arguments={
            "action": "step_blocked",
            "plan_id": "plan-1",
            "step_id": "transport",
            "blocker_type": "needs_source",
            "blocker_details": "Official pricing page unavailable.",
        },
    )

    assert result.status == "success"
    assert session_api.events[0]["event_type"] == "task_plan.step_blocked"


def test_plan_control_revise_records_full_plan_payload() -> None:
    session_api = _FakeSessionAPI(active_plan=_active_plan())
    revised_steps = [
        {
            **_active_plan()["steps"][0],
            "status": "completed",
            "output_summary": "Entry rules found.",
        }
    ]
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api),
        arguments={
            "action": "revise",
            "plan_id": "plan-1",
            "reason": "Transport is no longer needed.",
            "revised_steps": revised_steps,
        },
    )

    assert result.status == "success"
    assert session_api.events[0]["event_type"] == "task_plan.revised"
    assert session_api.events[0]["payload"]["plan"]["steps"][0]["step_id"] == "entry"


def test_plan_control_terminal_actions_record_canonical_events() -> None:
    for action, event_type in (
        ("abandon", "task_plan.abandoned"),
        ("complete", "task_plan.completed"),
    ):
        session_api = _FakeSessionAPI(active_plan=_active_plan())
        result = handle_plan_tool_call(
            loop_ctx=_Ctx(session_api=session_api),
            arguments={
                "action": action,
                "plan_id": "plan-1",
                "reason": "done",
            },
        )

        assert result.status == "success"
        assert session_api.events[0]["event_type"] == event_type


def test_plan_control_same_turn_declare_then_complete_uses_active_plan_override() -> (
    None
):
    session_api = _FakeSessionAPI()
    loop_ctx = _Ctx(session_api=session_api)

    declared = handle_plan_tool_call(
        loop_ctx=loop_ctx,
        arguments={
            "action": "declare",
            "plan_id": "plan-1",
            "objective": "Research and summarize",
            "steps": _active_plan()["steps"],
        },
    )
    completed = handle_plan_tool_call(
        loop_ctx=loop_ctx,
        arguments={
            "action": "complete",
            "plan_id": "plan-1",
            "reason": "done",
        },
    )

    assert declared.status == "success"
    assert completed.status == "success"
    assert [event["event_type"] for event in session_api.events] == [
        "task_plan.declared",
        "task_plan.completed",
    ]


def test_plan_control_rejects_unknown_step_with_invalid_event() -> None:
    session_api = _FakeSessionAPI(active_plan=_active_plan())
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api),
        arguments={
            "action": "step_completed",
            "plan_id": "plan-1",
            "step_id": "missing",
            "outcome": "success",
        },
    )

    assert result.status == "failed"
    assert session_api.events[0]["event_type"] == "task_plan.invalid_trailer"
    assert session_api.events[0]["payload"]["reason"] == "unknown_step_id"


def test_plan_control_rejects_fuzzy_step_id_without_repair() -> None:
    session_api = _FakeSessionAPI(
        active_plan={
            **_active_plan(),
            "steps": [{**_active_plan()["steps"][0], "step_id": "inspect_readme"}],
        }
    )
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api),
        arguments={
            "action": "step_completed",
            "plan_id": "plan-1",
            "step_id": "inspect-readme",
            "outcome": "success",
        },
    )

    assert result.status == "failed"
    assert session_api.events[0]["event_type"] == "task_plan.invalid_trailer"
    assert session_api.events[0]["payload"]["reason"] == "unknown_step_id"


def test_plan_control_does_not_match_step_description_as_id() -> None:
    session_api = _FakeSessionAPI(
        active_plan={
            **_active_plan(),
            "steps": [
                {
                    **_active_plan()["steps"][0],
                    "step_id": "entry",
                    "description": "read README",
                }
            ],
        }
    )
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api),
        arguments={
            "action": "step_completed",
            "plan_id": "plan-1",
            "step_id": "read README",
            "outcome": "success",
        },
    )

    assert result.status == "failed"
    assert session_api.events[0]["event_type"] == "task_plan.invalid_trailer"
    assert session_api.events[0]["payload"]["reason"] == "unknown_step_id"


def test_plan_control_does_not_infer_only_remaining_step() -> None:
    session_api = _FakeSessionAPI(
        active_plan={
            **_active_plan(),
            "steps": [{**_active_plan()["steps"][0], "step_id": "entry"}],
        }
    )
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api),
        arguments={
            "action": "step_completed",
            "plan_id": "plan-1",
            "step_id": "missing",
            "outcome": "success",
        },
    )

    assert result.status == "failed"
    assert [event["event_type"] for event in session_api.events] == [
        "task_plan.invalid_trailer"
    ]


def test_plan_control_invalid_step_does_not_auto_revise_plan() -> None:
    session_api = _FakeSessionAPI(active_plan=_active_plan())
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api),
        arguments={
            "action": "step_completed",
            "plan_id": "plan-1",
            "step_id": "missing",
            "outcome": "success",
        },
    )

    assert result.status == "failed"
    assert "task_plan.revised" not in {
        event["event_type"] for event in session_api.events
    }
