from typing import Any

from openminion.modules.llm.providers import ProviderToolCall
from openminion.modules.policy.adapters.composition import (
    SEAM_API_TOOLS,
    build_default_composition_boundary_adapter,
)
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED
from openminion.modules.task.scheduling.user_schedule import normalize_user_schedule
from openminion.modules.task.surface import (
    build_task_surface,
    resolve_task_surface_source,
)


class TaskControlOperationError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any]) -> None:
        self.code = code
        self.details = details
        super().__init__(message)

    @property
    def is_forbidden(self) -> bool:
        return self.code in {TOOL_ERROR_CONFIRM_REQUIRED, "POLICY_DENIED"}


def execute_task_control(
    *,
    runtime: Any,
    tool_name: str,
    arguments: dict[str, object],
    agent_id: str,
    session_id: str,
    request_id: str,
    confirm: bool = False,
) -> dict[str, Any]:
    batch = runtime.tools.execute_calls(
        [
            ProviderToolCall(
                name=tool_name,
                arguments=arguments,
                id=request_id,
                source="api.tasks",
            )
        ],
        context=ToolExecutionContext(
            channel="api",
            target="tasks",
            session_id=session_id or "tasks",
            metadata={
                "agent_id": agent_id,
                "trace_id": request_id,
                "origin": "api.tasks",
            },
            task_manager=resolve_task_surface_source(runtime),
            scheduler_readiness=runtime.scheduler_readiness,
            confirm=confirm,
            blast_radius_adapter=build_default_composition_boundary_adapter(
                seam_id=SEAM_API_TOOLS,
            ),
        ),
    )
    result = batch.results[0] if batch.results else None
    if result is None:
        raise TaskControlOperationError(
            "EXEC_ERROR", "task control produced no result", {}
        )
    if not result.ok:
        data = dict(result.data or {})
        details = data.get("details")
        raise TaskControlOperationError(
            str(data.get("error_code") or "EXEC_ERROR"),
            str(result.error or "task control failed"),
            dict(details) if isinstance(details, dict) else data,
        )
    return dict(result.data or {})


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
    normalized_schedule = normalize_user_schedule(schedule)
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
        "scheduler": runtime.scheduler_readiness(),
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
