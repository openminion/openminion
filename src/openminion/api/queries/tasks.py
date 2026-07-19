from __future__ import annotations

from typing import Any

from openminion.modules.task.surface import (
    build_task_surface,
    resolve_task_surface_source,
)


def list_tasks(
    *, runtime: Any, agent_id: str = "", session_id: str = "", limit: int = 50
) -> dict[str, Any]:
    return build_task_surface(
        resolve_task_surface_source(runtime),
        agent_id=agent_id,
        session_id=session_id,
        limit=limit,
    ).inventory()


def show_task(
    *,
    runtime: Any,
    task_id: str,
    agent_id: str = "",
    session_id: str = "",
    limit: int = 50,
) -> dict[str, Any] | None:
    return build_task_surface(
        resolve_task_surface_source(runtime),
        agent_id=agent_id,
        session_id=session_id,
        limit=limit,
    ).show_task(task_id)
