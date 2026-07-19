from __future__ import annotations

from typing import Any

from openminion.modules.task.surface import (
    build_task_surface,
    resolve_task_surface_source,
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
