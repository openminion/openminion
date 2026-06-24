from __future__ import annotations

from openminion.modules.task.schemas import (
    PlanDraft,
    PlanStepDraft,
    PlanStepStatus,
    ResumePointer,
    StepUpdateInput,
    TaskAttachPlanOp,
    TaskCreateInput,
    TaskCreateOp,
    TaskOps,
    TaskStatus,
)
from openminion.modules.task.runtime.service import InMemoryTaskCtl


def test_plan_attach_and_step_progression() -> None:
    ctl = InMemoryTaskCtl()

    task = ctl.create_task(TaskCreateInput(title="Ship feature"), trace_id="trace-1")
    plan = ctl.attach_plan(
        task.task_id,
        PlanDraft(
            plan_name="Ship sequence",
            steps=[
                PlanStepDraft(title="Implement", instruction="Write module"),
                PlanStepDraft(title="Validate", instruction="Run tests"),
            ],
        ),
        trace_id="trace-1",
    )

    first_step = plan.steps[0]
    second_step = plan.steps[1]

    ctl.step_update(
        task.task_id,
        first_step.step_id,
        StepUpdateInput(status=PlanStepStatus.DONE, note="implemented"),
        trace_id="trace-1",
    )

    task_after_first = ctl.get_task(task.task_id)
    assert task_after_first.status == TaskStatus.ACTIVE
    assert task_after_first.next_step_id == second_step.step_id

    ctl.step_update(
        task.task_id,
        second_step.step_id,
        StepUpdateInput(status=PlanStepStatus.DONE, note="validated"),
        trace_id="trace-1",
    )

    task_after_second = ctl.get_task(task.task_id)
    assert task_after_second.status == TaskStatus.DONE
    assert task_after_second.next_step_id is None


def test_pending_action_resume_returns_same_cursor() -> None:
    ctl = InMemoryTaskCtl()
    task = ctl.create_task(TaskCreateInput(title="Deploy"), trace_id="trace-2")
    plan = ctl.attach_plan(
        task.task_id,
        PlanDraft(
            steps=[PlanStepDraft(title="Deploy step", instruction="Run deploy")],
        ),
        trace_id="trace-2",
    )

    cursor = ResumePointer(
        task_id=task.task_id,
        plan_id=plan.plan_id,
        step_id=plan.steps[0].step_id,
        attempt=1,
        trace_id="trace-2",
        turn_id="turn-7",
        pack_id="pack-3",
    )

    pending = ctl.record_pending_action(
        policy_request_id="policy-123",
        cursor=cursor,
        reason="exec requires approval",
    )
    assert pending.policy_request_id == "policy-123"
    assert pending.resolved_at is None

    resumed = ctl.resume_pending_action(
        policy_request_id="policy-123",
        decision_id="decision-9",
        trace_id="trace-2",
    )

    assert resumed == cursor
    events = ctl.list_events()
    paused = next(event for event in events if event["type"] == "mission.paused")
    resumed_event = next(
        event for event in events if event["type"] == "mission.resumed"
    )
    assert paused["payload"]["pending_backlog_count"] == 1
    assert resumed_event["payload"]["pending_backlog_count"] == 0
    assert resumed_event["payload"]["resume_latency_ms"] >= 0


def test_apply_ops_emits_task_ops_telemetry_event() -> None:
    ctl = InMemoryTaskCtl()
    task_id = "task-plan-1"

    touched = ctl.apply_ops(
        TaskOps(
            ops=[
                TaskCreateOp(
                    input=TaskCreateInput(task_id=task_id, title="Ship feature")
                ),
                TaskAttachPlanOp(
                    task_id=task_id,
                    plan=PlanDraft(
                        plan_id="plan-1",
                        steps=[
                            PlanStepDraft(
                                step_id="step-1",
                                title="Implement",
                                instruction="Write module",
                            )
                        ],
                    ),
                ),
            ]
        ),
        trace_id="trace-ops",
    )

    assert touched == [task_id, task_id]
    telemetry = [
        event for event in ctl.list_events() if event["type"] == "task.ops.applied"
    ][-1]
    assert telemetry["payload"]["op_count"] == 2
    assert telemetry["payload"]["touched_count"] == 2
    assert telemetry["payload"]["duration_ms"] >= 0


def test_digest_stays_bounded() -> None:
    ctl = InMemoryTaskCtl()
    for idx in range(7):
        ctl.create_task(TaskCreateInput(title=f"Task {idx}"), trace_id=f"trace-{idx}")

    digest = ctl.get_digest(agent_id="agent-a", session_id="sess-a", limit=3)

    assert digest.max_items == 3
    assert len(digest.tasks_ready) == 3


def test_task_records_created_and_executing_mode_lineage() -> None:
    ctl = InMemoryTaskCtl()

    task = ctl.create_task(
        TaskCreateInput(title="Ship feature", created_by_mode="plan"),
        trace_id="trace-1",
    )
    plan = ctl.attach_plan(
        task.task_id,
        PlanDraft(
            plan_name="Ship sequence",
            steps=[PlanStepDraft(title="Implement", instruction="Write module")],
        ),
        trace_id="trace-1",
    )

    updated_plan = ctl.step_update(
        task.task_id,
        plan.steps[0].step_id,
        StepUpdateInput(status=PlanStepStatus.ACTIVE, executing_mode="plan"),
        trace_id="trace-1",
    )
    refreshed = ctl.get_task(task.task_id)

    assert task.created_by_mode == "plan"
    assert plan.created_by_mode == "plan"
    assert refreshed.executing_mode == "plan"
    assert updated_plan.steps[0].executing_mode == "plan"
