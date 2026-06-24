from typing import Any

from pydantic import BaseModel, Field

from openminion.modules.tool.contracts.model_ids import (
    MODEL_AGENT_GET,
    MODEL_AGENT_LIST,
    MODEL_TASK_DELEGATE,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.runtime.environment import (
    storage_path_from_context,
)


class AgentListArgs(BaseModel):
    """Arguments for ``agent.list``."""

    status: str = Field(
        default="",
        description=(
            "Optional status filter (e.g. 'registered', 'stopped'). "
            "Empty string returns all agents."
        ),
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Max agents to return (1..200).",
    )


class AgentGetArgs(BaseModel):
    """Arguments for ``agent.get``."""

    agent_id: str = Field(
        ...,
        min_length=1,
        description="Exact agent identifier to look up.",
    )


class TaskDelegateArgs(BaseModel):
    """Arguments for ``task.delegate``.

    The target is exactly ``agent_id`` (accept-or-fail; no capability
    inference). ``instruction`` is the goal handed to the sub-agent.
    """

    agent_id: str = Field(
        ...,
        min_length=1,
        description="Target sub-agent identifier.",
    )
    instruction: str = Field(
        ...,
        min_length=1,
        description="Instruction to delegate to the sub-agent.",
    )
    timeout_seconds: int = Field(
        default=120,
        ge=1,
        le=3600,
        description="Per-call timeout for the delegated turn.",
    )


def _agent_record_to_dict(record: Any) -> dict[str, Any]:
    return {
        "agent_id": getattr(record, "agent_id", ""),
        "display_name": getattr(record, "display_name", ""),
        "description": getattr(record, "description", ""),
        "config_path": getattr(record, "config_path", ""),
        "workspace_root": getattr(record, "workspace_root", ""),
        "tags": list(getattr(record, "tags", []) or []),
        "status": getattr(record, "status", ""),
        "registered_at": getattr(record, "registered_at", ""),
        "updated_at": getattr(record, "updated_at", ""),
    }


def _resolve_agent_registry(ctx: RuntimeContext) -> Any | None:
    """Resolve an ``AgentRegistryStore`` from runtime context."""
    storage_path = storage_path_from_context(ctx)
    if not storage_path:
        return None
    try:
        from openminion.modules.storage.runtime.registry_store import (
            AgentRegistryStore,
        )
    except Exception:
        return None
    try:
        return AgentRegistryStore(storage_path)
    except Exception:
        return None


def _h_agent_list(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = AgentListArgs.model_validate(args)
    registry = _resolve_agent_registry(ctx)
    if registry is None:
        return {
            "ok": True,
            "agents": [],
            "count": 0,
            "limit": int(validated.limit),
            "storage_unavailable": True,
        }
    status_filter = validated.status.strip() or None
    try:
        rows = registry.list_agents(status=status_filter)
    except Exception as exc:
        raise ToolRuntimeError(
            "EXEC_ERROR",
            f"Failed to list agents: {exc}",
            {"reason_code": "agent_registry_exec_error"},
        ) from exc
    effective_limit = max(1, min(int(validated.limit), 200))
    agents = [_agent_record_to_dict(row) for row in rows[:effective_limit]]
    return {
        "ok": True,
        "agents": agents,
        "count": len(agents),
        "limit": effective_limit,
    }


def _h_agent_get(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = AgentGetArgs.model_validate(args)
    agent_id = validated.agent_id.strip()
    registry = _resolve_agent_registry(ctx)
    if registry is None:
        raise ToolRuntimeError(
            "DEPENDENCY_MISSING",
            "Agent registry storage is not configured",
            {
                "reason_code": "agent_registry_unconfigured",
                "agent_id": agent_id,
            },
        )
    try:
        record = registry.get_agent(agent_id)
    except Exception as exc:
        raise ToolRuntimeError(
            "EXEC_ERROR",
            f"Failed to look up agent: {exc}",
            {"reason_code": "agent_registry_exec_error", "agent_id": agent_id},
        ) from exc
    if record is None:
        raise ToolRuntimeError(
            "NOT_FOUND",
            f"Agent {agent_id!r} is not registered",
            {"reason_code": "agent_not_found", "agent_id": agent_id},
        )
    return {
        "ok": True,
        "agent": _agent_record_to_dict(record),
    }


_A2A_NOT_FOUND_CODES = frozenset(
    {
        "AGENT_NOT_FOUND",
        "ROUTE_NOT_FOUND",
        "A2A_ROUTE_NOT_FOUND",
        "TARGET_NOT_FOUND",
        "NO_ROUTE",
    }
)


def _task_delegate_error_code(result_error_code: str, *, status: str) -> str:
    code = str(result_error_code or "").strip()
    if code in _A2A_NOT_FOUND_CODES:
        return "NOT_FOUND"
    if code == "TASK_DELEGATE_INVALID_ARGS":
        return "INVALID_ARGUMENT"
    if str(status or "").strip() == "running":
        return "EXEC_ERROR"
    return "UPSTREAM_ERROR"


def _h_task_delegate(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = TaskDelegateArgs.model_validate(args)
    seam = getattr(ctx, "a2a_delegate_api", None)
    if seam is None:
        raise ToolRuntimeError(
            "DEPENDENCY_MISSING",
            (
                "Sub-agent delegation is not available in this runtime. "
                "task.delegate requires an A2A delegation seam, which is not "
                "configured here."
            ),
            {
                "reason_code": "task_delegate_seam_unavailable",
                "agent_id": validated.agent_id,
            },
        )

    result = seam.delegate(
        agent_id=validated.agent_id,
        instruction=validated.instruction,
        timeout_seconds=int(validated.timeout_seconds),
    )

    if result.ok:
        return {
            "ok": True,
            "agent_id": result.target_agent_id or validated.agent_id,
            "status": result.status,
            "content": result.content,
            "outputs": dict(result.outputs or {}),
            "trace_id": result.trace_id,
            "task_id": result.task_id,
        }

    raise ToolRuntimeError(
        _task_delegate_error_code(result.error_code, status=result.status),
        result.error_message or "Delegation failed.",
        {
            "reason_code": "task_delegate_failed",
            "agent_id": validated.agent_id,
            "target_agent_id": result.target_agent_id,
            "delegate_status": result.status,
            "delegate_error_code": result.error_code,
            "trace_id": result.trace_id,
            "task_id": result.task_id,
        },
    )


def register(registry: ToolRegistry) -> None:
    registry.add(
        ToolSpec(
            name=MODEL_AGENT_LIST,
            args_model=AgentListArgs,
            min_scope="READ_ONLY",
            handler=_h_agent_list,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "agent", "delegation"),
            capabilities=("agent", "delegation"),
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_AGENT_GET,
            args_model=AgentGetArgs,
            min_scope="READ_ONLY",
            handler=_h_agent_get,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "agent", "delegation"),
            capabilities=("agent", "delegation"),
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_TASK_DELEGATE,
            args_model=TaskDelegateArgs,
            min_scope="WRITE_SAFE",
            handler=_h_task_delegate,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "agent", "delegation"),
            capabilities=("agent", "delegation"),
            block_under_readonly=True,
        )
    )


__all__ = [
    "AgentGetArgs",
    "AgentListArgs",
    "TaskDelegateArgs",
    "_h_agent_get",
    "_h_agent_list",
    "_h_task_delegate",
    "register",
]
