from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from ..interfaces import TASK_INTERFACE_VERSION
from .ops import apply_task_ops
from ..schemas import (
    PendingAction,
    PlanDraft,
    PlanRecord,
    PlanStepRecord,
    PlanStepStatus,
    ResumePointer,
    StepUpdateInput,
    TaskCreateInput,
    TaskDigest,
    TaskDigestTask,
    TaskEvent,
    TaskOps,
    TaskRecord,
    TaskStatus,
)

from openminion.base.time import utc_now as _utc_now


class TaskError(RuntimeError):
    """Base task module exception."""


class TaskNotFoundError(TaskError):
    """Raised when task_id is unknown."""


class PlanNotFoundError(TaskError):
    """Raised when plan is missing for a task."""


class StepNotFoundError(TaskError):
    """Raised when step_id is unknown in a plan."""


class PendingActionNotFoundError(TaskError):
    """Raised when policy_request_id is unknown."""


@dataclass(frozen=True)
class _IdempotencyRecord:
    task_id: str
    step_id: str
    status: PlanStepStatus
    note: str | None
    artifact_refs: tuple[str, ...]


class InMemoryTaskCtl:
    """Small deterministic implementation used for planning and tests."""

    contract_version = TASK_INTERFACE_VERSION

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._plans: dict[str, PlanRecord] = {}
        self._pending_by_policy_id: dict[str, PendingAction] = {}
        self._events: list[TaskEvent] = []
        self._step_idempotency: dict[str, _IdempotencyRecord] = {}

    def create_task(
        self, input: TaskCreateInput, *, trace_id: str | None = None
    ) -> TaskRecord:
        now = _utc_now()
        task_id = str(input.task_id or "").strip() or _new_id("tsk")
        existing = self._tasks.get(task_id)
        if existing is not None:
            return existing
        task = TaskRecord(
            task_id=task_id,
            title=input.title,
            description=input.description,
            status=TaskStatus.PENDING,
            due_at=input.due_at,
            scheduled_at=input.scheduled_at,
            wait_at=input.wait_at,
            labels=list(input.labels),
            created_by_mode=input.created_by_mode,
            executing_mode=None,
            created_at=now,
            updated_at=now,
        )
        self._tasks[task_id] = task
        self._emit("task.created", task_id=task_id, trace_id=trace_id)
        return task

    def attach_plan(
        self, task_id: str, draft: PlanDraft, *, trace_id: str | None = None
    ) -> PlanRecord:
        task = self.get_task(task_id)
        now = _utc_now()
        plan_id = str(draft.plan_id or "").strip() or _new_id("pln")
        existing_plan = self._plans.get(plan_id)
        if existing_plan is not None:
            return existing_plan

        steps: list[PlanStepRecord] = []
        for idx, step in enumerate(draft.steps, start=1):
            step_id = step.step_id or _new_id("stp")
            steps.append(
                PlanStepRecord(
                    step_id=step_id,
                    order_index=idx,
                    title=step.title,
                    instruction=step.instruction,
                    status=PlanStepStatus.PENDING,
                    note=None,
                    artifact_refs=[],
                    updated_at=now,
                )
            )

        plan = PlanRecord(
            plan_id=plan_id,
            task_id=task_id,
            plan_name=draft.plan_name,
            root_goal_id=draft.root_goal_id,
            created_by_mode=task.created_by_mode,
            steps=steps,
            created_at=now,
            updated_at=now,
        )
        self._plans[plan_id] = plan

        updated_task = task.model_copy(
            update={
                "current_plan_id": plan_id,
                "next_step_id": steps[0].step_id if steps else None,
                "updated_at": now,
            }
        )
        self._tasks[task_id] = updated_task

        self._emit("plan.created", task_id=task_id, plan_id=plan_id, trace_id=trace_id)
        self._emit("task.updated", task_id=task_id, plan_id=plan_id, trace_id=trace_id)
        return plan

    def step_update(
        self,
        task_id: str,
        step_id: str,
        input: StepUpdateInput,
        *,
        trace_id: str | None = None,
    ) -> PlanRecord:
        task = self.get_task(task_id)
        plan = self._get_plan(task.current_plan_id)

        idempotency_key = (input.idempotency_key or "").strip()
        if idempotency_key:
            existing = self._step_idempotency.get(idempotency_key)
            incoming = _IdempotencyRecord(
                task_id=task_id,
                step_id=step_id,
                status=input.status,
                note=input.note,
                artifact_refs=tuple(input.artifact_refs),
            )
            if existing is not None:
                if existing != incoming:
                    raise TaskError(
                        f"idempotency key {idempotency_key!r} was already used with a different payload"
                    )
                return plan
            self._step_idempotency[idempotency_key] = incoming

        now = _utc_now()
        updated_steps: list[PlanStepRecord] = []
        found = False
        for step in plan.steps:
            if step.step_id != step_id:
                updated_steps.append(step)
                continue
            found = True
            updated_steps.append(
                step.model_copy(
                    update={
                        "status": input.status,
                        "note": input.note,
                        "artifact_refs": list(input.artifact_refs),
                        "executing_mode": input.executing_mode,
                        "updated_at": now,
                    }
                )
            )

        if not found:
            raise StepNotFoundError(f"step not found: {step_id}")

        updated_plan = plan.model_copy(
            update={"steps": updated_steps, "updated_at": now}
        )
        self._plans[plan.plan_id] = updated_plan

        next_step = _next_actionable_step(updated_steps)
        next_step_id = next_step.step_id if next_step is not None else None

        task_status = _derive_task_status(task.status, updated_steps, input.status)
        updated_task = task.model_copy(
            update={
                "status": task_status,
                "next_step_id": next_step_id,
                "executing_mode": input.executing_mode,
                "updated_at": now,
            }
        )
        self._tasks[task_id] = updated_task

        event_type = {
            PlanStepStatus.DONE: "plan.step_done",
            PlanStepStatus.FAILED: "plan.step_failed",
            PlanStepStatus.BLOCKED: "plan.step_blocked",
            PlanStepStatus.ACTIVE: "plan.step_started",
            PlanStepStatus.PENDING: "plan.step_reset",
        }[input.status]
        self._emit(
            event_type,
            task_id=task_id,
            plan_id=plan.plan_id,
            step_id=step_id,
            trace_id=trace_id,
            payload={"next_step_id": next_step_id, "task_status": task_status.value},
        )
        self._emit(
            "mission.cursor_updated",
            task_id=task_id,
            plan_id=plan.plan_id,
            step_id=next_step_id,
            trace_id=trace_id,
        )
        return updated_plan

    def transition_task(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        trace_id: str | None = None,
    ) -> TaskRecord:
        task = self.get_task(task_id)
        now = _utc_now()
        updated = task.model_copy(update={"status": status, "updated_at": now})
        self._tasks[task_id] = updated
        self._emit(
            "task.status_changed",
            task_id=task_id,
            trace_id=trace_id,
            payload={"status": status.value},
        )
        return updated

    def apply_ops(self, task_ops: TaskOps, *, trace_id: str | None = None) -> list[str]:
        return apply_task_ops(
            task_ops,
            trace_id=trace_id,
            create_task=lambda input: self.create_task(input, trace_id=trace_id),
            attach_plan=lambda task_id, plan: self.attach_plan(
                task_id, plan, trace_id=trace_id
            ),
            step_update=lambda task_id, step_id, input: self.step_update(
                task_id, step_id, input, trace_id=trace_id
            ),
            transition_task=lambda task_id, status: self.transition_task(
                task_id, status, trace_id=trace_id
            ),
            emit=self._emit,
            unsupported_error=TaskError,
        )

    def get_task(self, task_id: str) -> TaskRecord:
        task = self._tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        return task

    def get_digest(
        self, *, agent_id: str, session_id: str, limit: int = 5
    ) -> TaskDigest:
        now = _utc_now()
        max_items = max(limit, 1)

        all_tasks = list(self._tasks.values())
        ready = [
            t for t in all_tasks if t.status in {TaskStatus.PENDING, TaskStatus.ACTIVE}
        ]
        active = [t for t in all_tasks if t.status == TaskStatus.ACTIVE]
        waiting = [t for t in all_tasks if t.status == TaskStatus.WAITING]

        ready = _sorted_tasks(ready)[:max_items]
        active = _sorted_tasks(active)[:max_items]

        current = active[0] if active else (ready[0] if ready else None)

        return TaskDigest(
            agent_id=agent_id,
            session_id=session_id,
            generated_at=now,
            tasks_ready=[self._to_digest_task(t) for t in ready],
            tasks_active=[self._to_digest_task(t) for t in active],
            current_task=self._to_digest_task(current) if current else None,
            blockers=[
                f"{t.task_id}:{t.title}" for t in _sorted_tasks(waiting)[:max_items]
            ],
            max_items=max_items,
        )

    def record_pending_action(
        self,
        *,
        policy_request_id: str,
        cursor: ResumePointer,
        reason: str | None = None,
    ) -> PendingAction:
        existing = self._pending_by_policy_id.get(policy_request_id)
        if existing is not None and existing.resolved_at is None:
            return existing

        pending = PendingAction(
            policy_request_id=policy_request_id,
            reason=reason,
            cursor=cursor,
            created_at=_utc_now(),
        )
        self._pending_by_policy_id[policy_request_id] = pending
        self._emit(
            "mission.paused",
            task_id=cursor.task_id,
            plan_id=cursor.plan_id,
            step_id=cursor.step_id,
            trace_id=cursor.trace_id,
            payload={
                "policy_request_id": policy_request_id,
                "reason": reason,
                "pending_backlog_count": self._pending_backlog_count(),
            },
        )
        return pending

    def resume_pending_action(
        self,
        *,
        policy_request_id: str,
        decision_id: str,
        trace_id: str | None = None,
    ) -> ResumePointer:
        pending = self._pending_by_policy_id.get(policy_request_id)
        if pending is None:
            raise PendingActionNotFoundError(
                f"policy request not found: {policy_request_id}"
            )

        if pending.resolved_at is None:
            resolved_at = _utc_now()
            latency_ms = int((resolved_at - pending.created_at).total_seconds() * 1000)
            pending = pending.model_copy(
                update={"resolved_at": resolved_at, "decision_id": decision_id}
            )
            self._pending_by_policy_id[policy_request_id] = pending
            self._emit(
                "mission.resumed",
                task_id=pending.cursor.task_id,
                plan_id=pending.cursor.plan_id,
                step_id=pending.cursor.step_id,
                trace_id=trace_id or pending.cursor.trace_id,
                payload={
                    "policy_request_id": policy_request_id,
                    "decision_id": decision_id,
                    "pending_backlog_count": self._pending_backlog_count(),
                    "resume_latency_ms": latency_ms,
                },
            )

        return pending.cursor

    def list_events(self) -> list[dict[str, object]]:
        return [event.model_dump(mode="json") for event in self._events]

    def _pending_backlog_count(self) -> int:
        return sum(
            1
            for pending in self._pending_by_policy_id.values()
            if pending.resolved_at is None
        )

    def _get_plan(self, plan_id: str | None) -> PlanRecord:
        if not plan_id:
            raise PlanNotFoundError("task has no attached plan")
        plan = self._plans.get(plan_id)
        if plan is None:
            raise PlanNotFoundError(f"plan not found: {plan_id}")
        return plan

    def _step_title(self, task: TaskRecord) -> str | None:
        if not task.current_plan_id or not task.next_step_id:
            return None
        plan = self._plans.get(task.current_plan_id)
        if plan is None:
            return None
        for step in plan.steps:
            if step.step_id == task.next_step_id:
                return step.title
        return None

    def _to_digest_task(self, task: TaskRecord) -> TaskDigestTask:
        return TaskDigestTask(
            task_id=task.task_id,
            title=task.title,
            status=task.status,
            next_step_id=task.next_step_id,
            next_step_title=self._step_title(task),
            due_at=task.due_at,
        )

    def _emit(
        self,
        event_type: str,
        *,
        task_id: str | None = None,
        plan_id: str | None = None,
        step_id: str | None = None,
        trace_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        self._events.append(
            TaskEvent(
                type=event_type,
                at=_utc_now(),
                task_id=task_id,
                plan_id=plan_id,
                step_id=step_id,
                trace_id=trace_id,
                payload=payload or {},
            )
        )


def _next_actionable_step(steps: Iterable[PlanStepRecord]) -> PlanStepRecord | None:
    for step in sorted(steps, key=lambda item: item.order_index):
        if step.status in {
            PlanStepStatus.PENDING,
            PlanStepStatus.ACTIVE,
            PlanStepStatus.BLOCKED,
        }:
            return step
    return None


def _derive_task_status(
    previous: TaskStatus,
    steps: list[PlanStepRecord],
    last_step_status: PlanStepStatus,
) -> TaskStatus:
    if last_step_status in {PlanStepStatus.BLOCKED, PlanStepStatus.FAILED}:
        return TaskStatus.WAITING
    if all(step.status == PlanStepStatus.DONE for step in steps):
        return TaskStatus.DONE
    if any(step.status == PlanStepStatus.ACTIVE for step in steps):
        return TaskStatus.ACTIVE
    if previous == TaskStatus.WAITING:
        return TaskStatus.ACTIVE
    return TaskStatus.ACTIVE


def _sorted_tasks(tasks: list[TaskRecord]) -> list[TaskRecord]:
    def _sort_key(task: TaskRecord) -> tuple[datetime, str, str]:
        due = task.due_at or datetime.max.replace(tzinfo=timezone.utc)
        return due, task.title.lower(), task.task_id

    return sorted(tasks, key=_sort_key)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"
