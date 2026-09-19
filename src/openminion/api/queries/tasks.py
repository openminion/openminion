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
    source = resolve_task_surface_source(runtime)
    task = build_task_surface(
        source,
        agent_id=agent_id,
        session_id=session_id,
        limit=limit,
    ).show_task(task_id)
    if task is not None and task.get("project"):
        from openminion.modules.task.project.reports import (
            build_project_report_from_task,
        )

        report = build_project_report_from_task(source, task_id=task_id)
        if session_id and report.project_run.session_id != session_id:
            return None
        task["project_report"] = report.model_dump(mode="json")
    return task
