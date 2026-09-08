from typing import Any, cast

from openminion.modules.task.scheduling.coordination import (
    schedule_user_task,
    scheduler_readiness_from_health,
)
from openminion.modules.task.surface import (
    build_task_surface,
    resolve_task_surface_source,
)
from openminion.services.health.service import collect_health_snapshot


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
    schedule = body.get("schedule")
    if not instruction:
        raise ValueError("instruction is required")
    if not isinstance(schedule, dict):
        raise ValueError("schedule is required")
    owner = resolve_task_surface_source(runtime)
    origin = {"channel": "api"}
    if session_id:
        origin["session_id"] = session_id
    scheduler = _scheduler_readiness(runtime)
    created = schedule_user_task(
        owner,
        instruction=instruction,
        schedule=schedule,
        agent_id=agent_id,
        name=str(body.get("name") or "") or None,
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
        "scheduler": scheduler,
    }


def _scheduler_readiness(runtime: Any) -> dict[str, Any]:
    try:
        health_payload = collect_health_snapshot(
            config_path=str(runtime.config_path),
            runtime=runtime,
        )
    except (AttributeError, OSError, RuntimeError, ValueError):
        health_payload = {}
    return cast(
        dict[str, Any],
        scheduler_readiness_from_health(
            health_payload,
            reachable=True,
            identity_matches=True,
        ),
    )


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
