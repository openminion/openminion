from typing import Any

from openminion.modules.task.constants import DEFAULT_TASK_MIN_EVERY_MS
from openminion.modules.task.scheduling.coordination import (
    scheduler_readiness_from_health,
)
from openminion.modules.task.scheduling.schedule import normalize_schedule
from openminion.modules.task.surface import (
    build_task_surface,
    resolve_task_surface_source,
)


def create_task(
    *,
    runtime: Any,
    body: dict[str, object] | None,
    agent_id: str,
    session_id: str = "",
) -> dict[str, Any]:
    if body is None:
        raise ValueError("JSON request body is required")
    unknown_fields = sorted(set(body) - {"instruction", "schedule", "name"})
    if unknown_fields:
        raise ValueError(f"unknown task fields: {', '.join(unknown_fields)}")
    instruction = str(body.get("instruction") or "").strip()
    name = " ".join(str(body.get("name") or instruction).split())
    if len(name) > 60:
        name = f"{name[:57].rstrip()}..."
    schedule = body.get("schedule")
    if not instruction:
        raise ValueError("instruction is required")
    if not isinstance(schedule, dict):
        raise ValueError("schedule is required")
    owner = resolve_task_surface_source(runtime)
    create = getattr(owner, "schedule_user_task", None)
    if not callable(create):
        raise RuntimeError("task scheduling is unavailable")
    origin = {"channel": "api"}
    if session_id:
        origin["session_id"] = session_id
    normalized_schedule = normalize_schedule(schedule)
    if (
        normalized_schedule["kind"] == "every"
        and int(normalized_schedule["every_ms"]) < DEFAULT_TASK_MIN_EVERY_MS
    ):
        raise ValueError(
            f"recurring interval must be at least {DEFAULT_TASK_MIN_EVERY_MS} ms"
        )
    created = create(
        name=name,
        instruction=instruction,
        schedule=normalized_schedule,
        agent_id=agent_id,
        origin=origin,
    )
    record = created["record"]
    job = created["job"]
    return {
        "ok": True,
        "task": build_task_surface(
            owner,
            agent_id=agent_id,
            session_id=session_id,
        ).show_task(record.task_id),
        "deduped": bool(created["deduped"]),
        "job_id": job.get("job_id"),
        "scheduler": scheduler_readiness_from_health(
            {},
            reachable=True,
            identity_matches=None,
        ),
    }


def apply_task_action(
    *,
    runtime: Any,
    task_id: str,
    action: str,
    agent_id: str = "",
    session_id: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    return build_task_surface(
        resolve_task_surface_source(runtime),
        agent_id=agent_id,
        session_id=session_id,
        limit=limit,
    ).apply_action(task_id=task_id, action=action)


def apply_pending_action(
    *,
    runtime: Any,
    decision_id: str,
    action: str,
    agent_id: str = "",
    session_id: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    return build_task_surface(
        resolve_task_surface_source(runtime),
        agent_id=agent_id,
        session_id=session_id,
        limit=limit,
    ).apply_action(task_id="", action=action, decision_id=decision_id)
