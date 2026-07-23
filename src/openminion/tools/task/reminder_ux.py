from __future__ import annotations

from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.loop.proactive_entrypoint import maybe_schedule_idle_tick
from .plugin import (
    _h_task_cancel,
    _h_task_list,
    _h_task_pause,
    _h_task_resume,
    _h_task_schedule,
    _h_task_show,
    _resolve_cron_store,
)


@dataclass(frozen=True)
class ReminderControlScenario:
    instruction: str
    name: str
    schedule: dict[str, Any]
    delivery_destination: str = "focus:operator"

    def __post_init__(self) -> None:
        if not str(self.instruction or "").strip():
            raise ValueError("instruction is required")
        if not str(self.name or "").strip():
            raise ValueError("name is required")
        if not isinstance(self.schedule, dict) or not self.schedule:
            raise ValueError("schedule is required")
        if not str(self.delivery_destination or "").strip():
            raise ValueError("delivery_destination is required")


@dataclass(frozen=True)
class ReminderControlScenarioResult:
    task_id: str
    schedule_time: str
    delivery_destination: str
    delivery_event_id: str
    history_event_id: str
    state_transitions: tuple[str, ...]
    final_state: str
    listed: bool
    shown: bool
    paused: bool
    resumed: bool
    cancelled: bool
    task_complete_supported: bool
    proof_mode: str = "hermetic_task_lifecycle"

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProactiveNoopScenarioResult:
    active_tick_id: str
    no_op_tick_id: str
    active_result: dict[str, Any]
    no_op_result: dict[str, Any]
    active_event_ids: tuple[str, ...]
    no_op_event_ids: tuple[str, ...]
    proof_mode: str = "hermetic_proactive_owner"

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)


class _InMemorySessionEvents:
    def __init__(self) -> None:
        self._events: dict[str, list[dict[str, Any]]] = {}

    def append_event(
        self,
        session_id: str,
        type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> str:
        events = self._events.setdefault(session_id, [])
        event_id = f"evt-{len(events) + 1}"
        events.append(
            {
                "event_id": event_id,
                "event_type": type,
                "payload": dict(payload or {}),
                **kwargs,
            }
        )
        return event_id

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._events.get(session_id, ()))


class _InMemoryCronStore:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}

    def add_cron_job(self, **kwargs: Any) -> str:
        job_id = str(kwargs.get("job_id") or kwargs.get("name") or "").strip()
        self.jobs[job_id] = {"job_id": job_id, **dict(kwargs)}
        return job_id

    def get_cron_job(self, job_id: str) -> dict[str, Any] | None:
        return self.jobs.get(job_id)

    def delete_cron_job(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)


def run_reminder_control_scenario(
    *,
    context: Any,
    scenario: ReminderControlScenario,
) -> ReminderControlScenarioResult:
    created = _h_task_schedule(
        {
            "instruction": scenario.instruction,
            "name": scenario.name,
            "schedule": dict(scenario.schedule),
        },
        context,
    )
    task_id = str(created["task_id"])
    listed = _task_is_listed(context=context, task_id=task_id)
    shown = _h_task_show({"task_id": task_id, "runs_limit": 5}, context)
    paused = _h_task_pause({"task_id": task_id}, context)
    resumed = _h_task_resume({"task_id": task_id}, context)
    history_event_id = _record_hermetic_run(context=context, task_id=task_id)
    delivery_event_id = f"hermetic-focus-delivery:{task_id}"
    cancelled = _h_task_cancel({"task_id": task_id}, context)
    final_state = _final_cron_state(context=context, task_id=task_id)
    return ReminderControlScenarioResult(
        task_id=task_id,
        schedule_time=str(created.get("next_due_at") or "scheduled"),
        delivery_destination=scenario.delivery_destination,
        delivery_event_id=delivery_event_id,
        history_event_id=history_event_id,
        state_transitions=(
            "scheduled",
            "listed",
            "shown",
            "paused",
            "resumed",
            "cancelled",
        ),
        final_state=final_state,
        listed=listed,
        shown=bool(shown.get("ok")),
        paused=bool(paused.get("paused")),
        resumed=bool(resumed.get("resumed")),
        cancelled=bool(cancelled.get("cancelled")),
        task_complete_supported=False,
    )


def run_proactive_noop_scenario() -> ProactiveNoopScenarioResult:
    active_session = _InMemorySessionEvents()
    no_op_session = _InMemorySessionEvents()
    active_cron = _InMemoryCronStore()
    no_op_cron = _InMemoryCronStore()
    active_result = maybe_schedule_idle_tick(
        cron_store=active_cron,
        session_api=active_session,
        runner=_runner_with_pae(enabled=True),
        session_id="daily-assistant-smoke-active",
        agent_id="agent-daily-smoke",
        plan_id="plan-active",
        trace_id="daily-smoke-active",
    )
    no_op_result = maybe_schedule_idle_tick(
        cron_store=no_op_cron,
        session_api=no_op_session,
        runner=_runner_with_pae(enabled=False),
        session_id="daily-assistant-smoke-noop",
        agent_id="agent-daily-smoke",
        plan_id="plan-noop",
        trace_id="daily-smoke-noop",
    )
    return ProactiveNoopScenarioResult(
        active_tick_id=str(active_result.get("job_id") or ""),
        no_op_tick_id=str(no_op_result.get("job_id") or "suppressed"),
        active_result=active_result,
        no_op_result=no_op_result,
        active_event_ids=_event_ids(active_session, "daily-assistant-smoke-active"),
        no_op_event_ids=_event_ids(no_op_session, "daily-assistant-smoke-noop"),
    )


def format_reminder_control_summary(result: ReminderControlScenarioResult) -> str:
    return "\n".join(
        (
            f"task_id: {result.task_id}",
            f"schedule_time: {result.schedule_time}",
            f"destination: {result.delivery_destination}",
            f"delivery_event_id: {result.delivery_event_id}",
            f"history_event_id: {result.history_event_id}",
            f"final_state: {result.final_state}",
            f"transitions: {', '.join(result.state_transitions)}",
        )
    )


def _runner_with_pae(*, enabled: bool) -> Any:
    pae = SimpleNamespace(
        enabled=enabled,
        interval_seconds=120 if enabled else 0,
        user_activity_grace_seconds=300,
        max_consecutive_noops=3,
    )
    profile = SimpleNamespace(
        agent_id="agent-daily-smoke",
        proactive_autonomous_entrypoint=pae,
    )
    return SimpleNamespace(profile=profile, options=None)


def _event_ids(session_api: _InMemorySessionEvents, session_id: str) -> tuple[str, ...]:
    return tuple(
        str(event.get("event_id") or "")
        for event in session_api.list_events(session_id)
    )


def _task_is_listed(*, context: Any, task_id: str) -> bool:
    listed = _h_task_list({"limit": 20}, context)
    tasks = listed.get("tasks") if isinstance(listed, dict) else None
    if not isinstance(tasks, list):
        return False
    return any(
        str(item.get("task_id") or "") == task_id
        for item in tasks
        if isinstance(item, dict)
    )


def _record_hermetic_run(*, context: Any, task_id: str) -> str:
    manager = _resolve_cron_store(context)
    run_id = manager.trigger_cron_run(task_id, due_at="2030-01-01T00:00:00Z")
    manager.finish_cron_run(
        run_id=run_id,
        state="finished",
        summary="Hermetic reminder lifecycle receipt recorded.",
    )
    return str(run_id)


def _final_cron_state(*, context: Any, task_id: str) -> str:
    store = _resolve_cron_store(context)
    row = store.get_cron_job(task_id)
    if isinstance(row, dict):
        state = str(row.get("state") or "").strip()
        if state:
            return state
    return "cancelled"


__all__ = [
    "ProactiveNoopScenarioResult",
    "ReminderControlScenario",
    "ReminderControlScenarioResult",
    "format_reminder_control_summary",
    "run_proactive_noop_scenario",
    "run_reminder_control_scenario",
]
