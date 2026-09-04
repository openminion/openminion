from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openminion.modules.brain.loop.tools.plan_control import (
    PLAN_CONTINUE_AUTONOMOUSLY_OUTPUT_KEY,
    append_plan_closeout_guidance,
    build_plan_tool_spec,
    complete_active_plan_if_ready,
    completable_active_plan_id,
    handle_plan_tool_call,
)
from openminion.modules.brain.schemas import ActionResult
from openminion.modules.brain.loop.tools.contracts import AdaptiveToolLoopState
from openminion.modules.llm.schemas import Message
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
    assert "criterion_ids" in spec.input_schema["properties"]
    assert "revision_id" in spec.input_schema["properties"]
    assert "predecessor_revision_id" in spec.input_schema["properties"]
    assert "verifier_refs" in spec.input_schema["properties"]


def test_plan_closeout_guidance_repeats_the_original_request() -> None:
    loop_state = AdaptiveToolLoopState(
        messages=[
            Message(role="user", content="Return exactly DONE."),
            Message(role="user", content="continue"),
        ]
    )

    append_plan_closeout_guidance(
        loop_state,
        {"action": "complete"},
        ActionResult(command_id="cmd-plan", status="success", summary="complete"),
    )

    assert "Original request:\nReturn exactly DONE." in loop_state.messages[-1].content


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
            "criterion_ids": ["criterion-source", "criterion-summary"],
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
    assert result.outputs["task_plan"]["criterion_ids"] == [
        "criterion-source",
        "criterion-summary",
    ]


def test_plan_control_declare_rejects_stringified_steps() -> None:
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

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "PLAN_VALIDATION_FAILED"
    assert session_api.events[-1]["event_type"] == "task_plan.invalid_trailer"


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


def test_plan_control_redeclare_preserves_in_progress_step() -> None:
    active_plan = _active_plan()
    active_plan["steps"][0].update(
        status="in_progress",
        output_summary="Inspection underway.",
    )
    session_api = _FakeSessionAPI(active_plan=active_plan)

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
    entry = session_api.events[0]["payload"]["plan"]["steps"][0]
    assert entry["status"] == "in_progress"
    assert entry["output_summary"] == "Inspection underway."


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


def test_plan_control_final_step_does_not_request_another_autonomous_turn() -> None:
    plan = {
        **_active_plan(),
        "continue_plan_autonomously": True,
        "steps": [
            {
                **_active_plan()["steps"][0],
                "status": "in_progress",
            }
        ],
    }
    session_api = _FakeSessionAPI(active_plan=plan)

    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api),
        arguments={
            "action": "step_completed",
            "plan_id": "plan-1",
            "step_id": "entry",
            "outcome": "success",
            "continue_plan_autonomously": True,
        },
    )

    assert result.status == "success"
    assert PLAN_CONTINUE_AUTONOMOUSLY_OUTPUT_KEY not in result.outputs
    assert [event["event_type"] for event in session_api.events] == [
        "task_plan.step_completed"
    ]


def test_plan_control_identifies_plan_ready_for_explicit_completion() -> None:
    plan = _active_plan()
    for step in plan["steps"]:
        step["status"] = "completed"

    assert completable_active_plan_id(_Ctx(session_api=_FakeSessionAPI(plan))) == (
        "plan-1"
    )
    assert (
        completable_active_plan_id(_Ctx(session_api=_FakeSessionAPI(_active_plan())))
        == ""
    )


def test_plan_control_reconciles_completed_steps_at_success() -> None:
    plan = _active_plan()
    for step in plan["steps"]:
        step["status"] = "completed"
    session_api = _FakeSessionAPI(plan)

    payload = complete_active_plan_if_ready(_Ctx(session_api=session_api))

    assert payload == {
        "plan_id": "plan-1",
        "reason": "all_steps_completed_at_success",
    }
    assert session_api.events[-1]["event_type"] == "task_plan.completed"
    assert session_api.events[-1]["payload"] == {
        **payload,
        "source": "plan_tool",
    }


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
            "revision_id": "revision-1",
            "criterion_ids": ["criterion-source"],
            "verifier_refs": ["verify:failed-1"],
            "revised_steps": revised_steps,
        },
    )

    assert result.status == "success"
    assert session_api.events[0]["event_type"] == "task_plan.revised"
    assert session_api.events[0]["payload"]["plan"]["steps"][0]["step_id"] == "entry"
    assert session_api.events[0]["payload"]["revision"]["revision_id"] == ("revision-1")
    assert result.outputs["task_plan.revision"]["revision_id"] == "revision-1"
    assert result.outputs["task_plan.revision"]["verifier_refs"] == ["verify:failed-1"]


def test_plan_control_terminal_actions_record_canonical_events() -> None:
    for action, event_type in (("abandon", "task_plan.abandoned"),):
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


def test_plan_control_complete_rejects_unresolved_steps() -> None:
    session_api = _FakeSessionAPI(active_plan=_active_plan())
    result = handle_plan_tool_call(
        loop_ctx=_Ctx(session_api=session_api),
        arguments={
            "action": "complete",
            "plan_id": "plan-1",
            "reason": "done",
        },
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "PLAN_STEPS_UNRESOLVED"
    assert result.error.details["step_ids"] == ["entry", "transport"]
    assert session_api.events == []


def test_plan_control_same_turn_declare_then_complete_preserves_active_plan() -> None:
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
    assert completed.status == "failed"
    assert completed.error is not None
    assert completed.error.code == "PLAN_STEPS_UNRESOLVED"
    assert [event["event_type"] for event in session_api.events] == [
        "task_plan.declared"
    ]
    assert loop_ctx._plan_tool_active_plan_override is not None


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
