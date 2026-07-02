from __future__ import annotations

from datetime import datetime
from typing import Iterable
from uuid import uuid4

from openminion.modules.storage.record_store import RecordStore
from ..storage.repository import SqlTaskRepository
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


class SqlTaskCtl:
    """Durable implementation using SQL storage instead of in-memory."""

    contract_version = TASK_INTERFACE_VERSION

    def __init__(self, store: RecordStore) -> None:
        self._repo = SqlTaskRepository(store=store)
        self._events: list[TaskEvent] = []

    def _dict_to_task_record(self, row: dict[str, object]) -> TaskRecord:
        def parse_datetime(s: str | None) -> datetime | None:
            if s is None or s == "":
                return None
            if isinstance(s, str):
                iso_string = (
                    s.replace("Z", "+00:00")
                    if isinstance(s, str) and s.endswith("Z")
                    else s
                )
                return datetime.fromisoformat(iso_string)
            return s

        status_str = str(row.get("status", "PENDING"))
        status = (
            TaskStatus(status_str)
            if status_str in [e.value for e in TaskStatus.__members__.values()]
            else TaskStatus.PENDING
        )

        return TaskRecord(
            task_id=str(row["task_id"]) if row["task_id"] is not None else "",
            title=str(row["title"]) if row["title"] is not None else "",
            description=row["description"],
            status=status,
            due_at=parse_datetime(row.get("due_at")),
            scheduled_at=parse_datetime(row.get("scheduled_at")),
            wait_at=parse_datetime(row.get("wait_at")),
            created_by_mode=row.get("created_by_mode"),
            executing_mode=row.get("executing_mode"),
            current_plan_id=row["current_plan_id"],
            next_step_id=row["next_step_id"],
            labels=[],
            created_at=parse_datetime(row.get("created_at")) or _utc_now(),
            updated_at=parse_datetime(row.get("updated_at")) or _utc_now(),
        )

    def _dict_to_plan_record(self, row: dict[str, object]) -> PlanRecord:
        def to_datetime(dt_or_none, default=None):
            if dt_or_none is None:
                return default or _utc_now()
            return datetime.fromisoformat(str(dt_or_none).replace("Z", "+00:00"))

        plan_id = str(row["plan_id"]) if row["plan_id"] is not None else ""
        task_id = str(row["task_id"]) if row["task_id"] is not None else ""
        plan_name = str(row["plan_name"]) if row.get("plan_name") is not None else None

        step_dicts = self._repo.get_steps_for_plan(plan_id)
        steps = [self._dict_to_step_record(step_dict) for step_dict in step_dicts]

        return PlanRecord(
            plan_id=plan_id,
            task_id=task_id,
            plan_name=plan_name,
            root_goal_id=(
                str(row.get("root_goal_id", "")).strip() or None
                if row.get("root_goal_id") is not None
                else None
            ),
            created_by_mode=row.get("created_by_mode"),
            steps=steps,
            created_at=to_datetime(row.get("created_at")),
            updated_at=to_datetime(row.get("updated_at")),
        )

    def _dict_to_step_record(self, row: dict[str, object]) -> PlanStepRecord:
        import json

        def safe_str(obj, default="") -> str:
            return str(obj) if obj is not None else default

        def safe_int(obj, default=0) -> int:
            try:
                return int(obj) if obj is not None else default
            except (ValueError, TypeError):
                return default

        def safe_datetime(str_val, default=None) -> datetime:
            if str_val is None or str_val == "":
                return default or _utc_now()
            str_value = str(str_val)
            try:
                str_value = str_value.replace("Z", "+00:00")
                if (
                    "." not in str_value
                    and "+" not in str_value
                    and len(str_value) > 19
                ):
                    str_value += "+00:00"
                return datetime.fromisoformat(str_value)
            except ValueError:
                return default or _utc_now()

        def safe_json_list(obj, default=None):
            fallback = [] if default is None else default
            if obj is None:
                return fallback
            try:
                return json.loads(str(obj)) if obj != "" else []
            except (TypeError, json.JSONDecodeError):
                return fallback

        status_val = safe_str(row.get("status"), "PENDING")
        status_mapping = {
            "PENDING": PlanStepStatus.PENDING,
            "ACTIVE": PlanStepStatus.ACTIVE,
            "DONE": PlanStepStatus.DONE,
            "FAILED": PlanStepStatus.FAILED,
            "BLOCKED": PlanStepStatus.BLOCKED,
        }
        final_status = status_mapping.get(status_val, PlanStepStatus.PENDING)

        return PlanStepRecord(
            step_id=safe_str(row.get("step_id", "")),
            order_index=safe_int(row.get("order_index", 1)),
            title=safe_str(row.get("title", "")),
            instruction=safe_str(row.get("instruction", "")),
            status=final_status,
            note=row.get("note"),
            artifact_refs=safe_json_list(row.get("artifact_refs")),
            executing_mode=row.get("executing_mode"),
            updated_at=safe_datetime(row.get("updated_at")),
        )

    def _dict_to_pending_action(self, row: dict[str, object]) -> PendingAction:
        def parse_datetime(s: str | None) -> datetime | None:
            if not s:
                return None
            return datetime.fromisoformat(s.replace("Z", "+00:00"))

        cursor = ResumePointer(
            task_id=str(row["task_id"]),
            plan_id=str(row["plan_id"]),
            step_id=str(row["step_id"]),
            attempt=int(row["attempt"]) if row["attempt"] is not None else 1,
            trace_id=str(row["trace_id"]),
            turn_id=row["turn_id"],
            pack_id=row["pack_id"],
        )

        return PendingAction(
            pending_action_id=str(row["pending_action_id"]),
            policy_request_id=str(row["policy_request_id"]),
            state=str(row["state"]),  # Using the literal string value
            reason=row["reason"],
            cursor=cursor,
            created_at=parse_datetime(row["created_at"]),
            resolved_at=parse_datetime(row["resolved_at"]),
            decision_id=row["decision_id"],
        )

    def create_task(
        self, input: TaskCreateInput, *, trace_id: str | None = None
    ) -> TaskRecord:
        now = _utc_now()
        task_id = str(input.task_id or "").strip() or _new_id("tsk")
        existing = self._repo.get_task(task_id)
        if existing is not None:
            return self._dict_to_task_record(existing)

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
            current_plan_id=None,
            next_step_id=None,
            created_at=now,
            updated_at=now,
        )

        self._repo.create_task(
            task_id=task.task_id,
            title=task.title,
            description=task.description,
            status=task.status,
            due_at=task.due_at,
            scheduled_at=task.scheduled_at,
            wait_at=task.wait_at,
            created_by_mode=task.created_by_mode,
            executing_mode=task.executing_mode,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

        self._emit("task.created", task_id=task_id, trace_id=trace_id)
        return task

    def attach_plan(
        self, task_id: str, draft: PlanDraft, *, trace_id: str | None = None
    ) -> PlanRecord:
        task_row = self._repo.get_task(task_id)
        if task_row is None:
            raise TaskNotFoundError(f"task not found: {task_id}")

        task = self._dict_to_task_record(task_row)
        now = _utc_now()
        plan_id = str(draft.plan_id or "").strip() or _new_id("pln")
        existing_plan_row = self._repo.get_plan(plan_id)
        if existing_plan_row is not None:
            return self._dict_to_plan_record(existing_plan_row)

        steps: list[PlanStepRecord] = []
        for idx, step in enumerate(draft.steps, start=1):
            step_id = step.step_id or _new_id("stp")

            step_record = PlanStepRecord(
                step_id=step_id,
                order_index=idx,
                title=step.title,
                instruction=step.instruction,
                status=PlanStepStatus.PENDING,
                note=None,
                artifact_refs=[],
                executing_mode=None,
                updated_at=now,
            )

            self._repo.create_step(
                step_id=step_record.step_id,
                plan_id=plan_id,
                order_index=step_record.order_index,
                title=step_record.title,
                instruction=step_record.instruction,
                status=step_record.status,
                note=step_record.note,
                artifact_refs=step_record.artifact_refs,
                executing_mode=step_record.executing_mode,
                updated_at=step_record.updated_at,
            )
            steps.append(step_record)

        plan = PlanRecord(
            plan_id=plan_id,
            task_id=task_id,
            plan_name=draft.plan_name,
            created_by_mode=task.created_by_mode,
            steps=steps,
            created_at=now,
            updated_at=now,
        )

        self._repo.create_plan(
            plan_id=plan.plan_id,
            task_id=plan.task_id,
            plan_name=plan.plan_name,
            root_goal_id=plan.root_goal_id,
            created_by_mode=plan.created_by_mode,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )

        self._repo.attach_plan_to_task(task_id, plan_id)

        self._repo.update_task(
            task_id=task_id,
            current_plan_id=plan_id,
            next_step_id=steps[0].step_id if steps else None,
            executing_mode=task.executing_mode,
            updated_at=now,
        )

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
        task_row = self._repo.get_task(task_id)
        if task_row is None:
            raise TaskNotFoundError(f"task not found: {task_id}")

        task = self._dict_to_task_record(task_row)

        if not task.current_plan_id:
            raise PlanNotFoundError(f"task {task_id} has no current plan")

        plan_row = self._repo.get_plan(task.current_plan_id)
        if plan_row is None:
            raise PlanNotFoundError(f"plan not found: {task.current_plan_id}")

        plan = self._dict_to_plan_record(plan_row)

        idempotency_key = (input.idempotency_key or "").strip()
        if idempotency_key:
            existing = self._repo.get_idempotency_record(idempotency_key)
            if existing is not None:
                plan_row = self._repo.get_plan(task.current_plan_id)
                return self._dict_to_plan_record(plan_row)

            self._repo.record_idempotency(
                idempotency_key=idempotency_key,
                task_id=task_id,
                step_id=step_id,
                status=input.status,
                note=input.note,
                artifact_refs=input.artifact_refs,
            )

        self._repo.update_step(
            step_id=step_id,
            status=input.status,
            note=input.note,
            artifact_refs=input.artifact_refs,
            executing_mode=input.executing_mode,
            updated_at=_utc_now(),
        )

        plan_row = self._repo.get_plan(task.current_plan_id)
        plan = self._dict_to_plan_record(plan_row)

        next_step_id = _next_actionable_step(plan.steps)
        next_step_id = next_step_id.step_id if next_step_id is not None else None

        task_status = _derive_task_status(task.status, plan.steps, input.status)
        self._repo.update_task(
            task_id=task_id,
            status=task_status,
            next_step_id=next_step_id,
            executing_mode=input.executing_mode,
            updated_at=_utc_now(),
        )

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
        return plan

    def transition_task(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        trace_id: str | None = None,
    ) -> TaskRecord:
        task_row = self._repo.get_task(task_id)
        if task_row is None:
            raise TaskNotFoundError(f"task not found: {task_id}")

        updated_at = _utc_now()
        self._repo.update_task(task_id=task_id, status=status, updated_at=updated_at)

        refreshed_row = self._repo.get_task(task_id)
        updated_task = self._dict_to_task_record(refreshed_row)

        self._emit(
            "task.status_changed",
            task_id=task_id,
            trace_id=trace_id,
            payload={"status": status.value},
        )
        return updated_task

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
        task_row = self._repo.get_task(task_id)
        if task_row is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        return self._dict_to_task_record(task_row)

    def get_digest(
        self, *, agent_id: str, session_id: str, limit: int = 5
    ) -> TaskDigest:
        now = _utc_now()
        max_items = max(limit, 1)

        ready_rows = self._repo.get_tasks_ready(limit=max_items)
        active_rows = self._repo.get_tasks_active(limit=max_items)
        waiting_rows = self._repo.get_tasks_waiting(limit=max_items)

        ready_tasks = [self._dict_to_task_record(row) for row in ready_rows]
        active_tasks = [self._dict_to_task_record(row) for row in active_rows]

        current = (
            active_tasks[0]
            if active_tasks
            else (ready_tasks[0] if ready_tasks else None)
        )

        return TaskDigest(
            agent_id=agent_id,
            session_id=session_id,
            generated_at=now,
            tasks_ready=[self._to_digest_task(t) for t in ready_tasks],
            tasks_active=[self._to_digest_task(t) for t in active_tasks],
            current_task=self._to_digest_task(current) if current else None,
            blockers=[
                f"{t.task_id}:{t.title}"
                for t in [self._dict_to_task_record(r) for r in waiting_rows][
                    :max_items
                ]
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
        existing = self._repo.get_pending_action(policy_request_id)
        if existing is not None and existing.get("resolved_at") is None:
            existing_pa = self._dict_to_pending_action(existing)
            return existing_pa

        now = _utc_now()
        pending_action_id = _new_id("pa")

        self._repo.record_pending_action(
            pending_action_id=pending_action_id,
            policy_request_id=policy_request_id,
            state="NEEDS_APPROVAL",
            reason=reason,
            cursor=cursor,
            created_at=now,
        )

        pending = PendingAction(
            pending_action_id=pending_action_id,
            policy_request_id=policy_request_id,
            reason=reason,
            cursor=cursor,
            created_at=now,
        )

        self._emit(
            "mission.paused",
            task_id=cursor.task_id,
            plan_id=cursor.plan_id,
            step_id=cursor.step_id,
            trace_id=cursor.trace_id,
            payload={
                "policy_request_id": policy_request_id,
                "reason": reason,
                "pending_backlog_count": self._repo.count_pending_actions(),
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
        pending_row = self._repo.get_pending_action(policy_request_id)
        if pending_row is None:
            raise PendingActionNotFoundError(
                f"policy request not found: {policy_request_id}"
            )

        if pending_row.get("resolved_at") is None:
            resolved_at = _utc_now()
            pending = self._dict_to_pending_action(pending_row)
            latency_ms = int((resolved_at - pending.created_at).total_seconds() * 1000)
            # Record the resolution in DB
            self._repo.update_pending_action(
                policy_request_id=policy_request_id,
                resolved_at=resolved_at,
                decision_id=decision_id,
            )

            self._emit(
                "mission.resumed",
                task_id=pending.cursor.task_id,
                plan_id=pending.cursor.plan_id,
                step_id=pending.cursor.step_id,
                trace_id=trace_id or pending.cursor.trace_id,
                payload={
                    "policy_request_id": policy_request_id,
                    "decision_id": decision_id,
                    "pending_backlog_count": self._repo.count_pending_actions(),
                "resume_latency_ms": latency_ms,
            },
        )

        cursor = ResumePointer(
            task_id=pending_row["task_id"],
            plan_id=pending_row["plan_id"],
            step_id=pending_row["step_id"],
            attempt=int(pending_row["attempt"]),
            trace_id=pending_row["trace_id"],
            turn_id=pending_row.get("turn_id"),
            pack_id=pending_row.get("pack_id"),
        )

        return cursor

    def list_events(self) -> list[dict[str, object]]:
        return [event.model_dump(mode="json") for event in self._events]

    def _to_digest_task(self, task: TaskRecord) -> TaskDigestTask:
        return TaskDigestTask(
            task_id=task.task_id,
            title=task.title,
            status=task.status,
            next_step_id=task.next_step_id,
            next_step_title=self._step_title(task),
            due_at=task.due_at,
        )

    def _step_title(self, task: TaskRecord) -> str | None:
        if not task.current_plan_id or not task.next_step_id:
            return None

        step_row = self._repo.get_step(task.next_step_id)
        if step_row:
            return str(step_row["title"])

        return None

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
        from ..schemas import TaskEvent

        event = TaskEvent(
            type=event_type,
            at=_utc_now(),
            task_id=task_id,
            plan_id=plan_id,
            step_id=step_id,
            trace_id=trace_id,
            payload=payload or {},
        )
        self._events.append(event)


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

    all_done = all(step.status == PlanStepStatus.DONE for step in steps if step)
    if all_done:
        return TaskStatus.DONE

    has_active = any(step.status == PlanStepStatus.ACTIVE for step in steps)
    if has_active:
        return TaskStatus.ACTIVE

    if previous == TaskStatus.WAITING:
        return TaskStatus.ACTIVE

    return TaskStatus.ACTIVE


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"
