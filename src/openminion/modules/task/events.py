from __future__ import annotations

from typing import Callable

from .schemas import TaskEvent


class TaskEventPublisher:
    """Handles publication of task/plan-related events for observability and audit."""

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        """Add an event handler"""
        self._handlers.append(handler)

    def unsubscribe(self, handler: EventHandler) -> None:
        """Remove an event handler"""
        if handler in self._handlers:
            self._handlers.remove(handler)

    def publish(self, event: TaskEvent) -> None:
        """Publish an event to all subscribed handlers."""
        for handler in self._handlers:
            try:
                handler(event)
            except Exception:
                pass


EventHandler = Callable[[TaskEvent], None]


TASK_CREATED = "task.created"
TASK_UPDATED = "task.updated"
TASK_STATUS_CHANGED = "task.status_changed"

PLAN_CREATED = "plan.created"
PLAN_STEP_STARTED = "plan.step_started"
PLAN_STEP_DONE = "plan.step_done"
PLAN_STEP_FAILED = "plan.step_failed"
PLAN_STEP_BLOCKED = "plan.step_blocked"
PLAN_STEP_RESET = "plan.step_reset"

MISSION_PAUSED = "mission.paused"
MISSION_RESUMED = "mission.resumed"
MISSION_CURSOR_UPDATED = "mission.cursor_updated"


def create_task_created_event(
    task_id: str, title: str, trace_id: str | None = None
) -> TaskEvent:
    """Create a task.created event."""
    from datetime import datetime, timezone
    from .schemas import TaskStatus

    return TaskEvent(
        type=TASK_CREATED,
        at=datetime.now(timezone.utc),
        task_id=task_id,
        payload={"title": title, "status": TaskStatus.PENDING.value},
        trace_id=trace_id,
    )


def create_plan_created_event(
    task_id: str, plan_id: str, plan_name: str | None, trace_id: str | None = None
) -> TaskEvent:
    """Create a plan.created event."""
    from datetime import datetime, timezone

    return TaskEvent(
        type=PLAN_CREATED,
        at=datetime.now(timezone.utc),
        task_id=task_id,
        plan_id=plan_id,
        payload={"plan_name": plan_name},
        trace_id=trace_id,
    )


def create_mission_cursor_updated_event(
    task_id: str, plan_id: str, step_id: str | None, trace_id: str | None = None
) -> TaskEvent:
    """Create a mission.cursor_updated event."""
    from datetime import datetime, timezone

    return TaskEvent(
        type=MISSION_CURSOR_UPDATED,
        at=datetime.now(timezone.utc),
        task_id=task_id,
        plan_id=plan_id,
        step_id=step_id,
        payload={"update_type": "cursor", "next_step_id": step_id},
        trace_id=trace_id,
    )


def create_mission_paused_event(
    task_id: str,
    plan_id: str,
    step_id: str,
    policy_request_id: str,
    reason: str | None = None,
    trace_id: str | None = None,
) -> TaskEvent:
    """Create a mission.paused event."""
    from datetime import datetime, timezone

    return TaskEvent(
        type=MISSION_PAUSED,
        at=datetime.now(timezone.utc),
        task_id=task_id,
        plan_id=plan_id,
        step_id=step_id,
        payload={
            "pause_reason": "needs_approval",
            "policy_request_id": policy_request_id,
            "approval_reason": reason,
        },
        trace_id=trace_id,
    )


def create_mission_resumed_event(
    task_id: str,
    plan_id: str,
    step_id: str,
    policy_request_id: str,
    decision_id: str,
    trace_id: str | None = None,
) -> TaskEvent:
    """Create a mission.resumed event."""
    from datetime import datetime, timezone

    return TaskEvent(
        type=MISSION_RESUMED,
        at=datetime.now(timezone.utc),
        task_id=task_id,
        plan_id=plan_id,
        step_id=step_id,
        payload={
            "resume_type": "approved",
            "policy_request_id": policy_request_id,
            "decision_id": decision_id,
        },
        trace_id=trace_id,
    )
