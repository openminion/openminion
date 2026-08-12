from typing import Any

from pydantic import BaseModel, Field, model_validator

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
from openminion.modules.brain.execution.worktree_children import (
    accept_child_worktree_artifact,
    reject_child_worktree_artifact,
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

    mode: str = Field(
        default="sync",
        description=(
            "Delegation lifecycle action. Use sync or async to start child work; "
            "use status, resume, or cancel with task_id; use accept or reject "
            "only after a child_artifact is returned. There is no create mode."
        ),
    )
    agent_id: str = Field(
        default="",
        description="Exact target sub-agent identifier for sync/async delegation.",
    )
    instruction: str = Field(
        default="",
        description="Instruction to delegate to the exact sub-agent for sync/async.",
    )
    task_id: str = Field(
        default="",
        description="A resumable async task/job handle for status, resume, or cancel.",
    )
    timeout_seconds: int = Field(
        default=120,
        ge=1,
        le=3600,
        description="Per-call timeout for the delegated turn.",
    )
    child_artifact: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Durable child worktree artifact record returned by delegated "
            "code-bearing work; required for accept/reject."
        ),
    )
    workspace_root: str = Field(
        default="",
        description="Parent repository root; required only when accepting artifacts.",
    )

    @model_validator(mode="after")
    def _validate_action_fields(self) -> "TaskDelegateArgs":
        normalized_mode = self.mode.strip().lower()
        allowed = {"sync", "async", "status", "resume", "cancel", "accept", "reject"}
        if normalized_mode not in allowed:
            raise ValueError(
                "mode must be one of: sync, async, status, resume, cancel, "
                "accept, reject"
            )
        self.mode = normalized_mode
        if normalized_mode in {"sync", "async"}:
            if not self.agent_id.strip() or not self.instruction.strip():
                raise ValueError(
                    "agent_id and instruction are required for sync/async delegation"
                )
            return self
        if normalized_mode in {"accept", "reject"}:
            if not self.child_artifact:
                raise ValueError("child_artifact is required for accept/reject")
            if normalized_mode == "accept" and not self.workspace_root.strip():
                raise ValueError("workspace_root is required for accept")
            return self
        if not self.task_id.strip():
            raise ValueError("task_id is required for status/resume/cancel")
        return self


def _agent_record_to_dict(record: Any) -> dict[str, Any]:
    status = str(getattr(record, "status", "") or "").strip()
    return {
        "agent_id": getattr(record, "agent_id", ""),
        "display_name": getattr(record, "display_name", ""),
        "description": getattr(record, "description", ""),
        "config_path": getattr(record, "config_path", ""),
        "workspace_root": getattr(record, "workspace_root", ""),
        "tags": list(getattr(record, "tags", []) or []),
        "status": status,
        "configured": False,
        "registry_present": True,
        "hot": False,
        "heartbeat_active": False,
        "available": True,
        "running": False,
        "stopped": status in {"", "registered", "stopped", "starting", "stopping"},
        "unknown": False,
        "state": status or "registered",
        "capabilities": ["delegate.sync"],
        "registered_at": getattr(record, "registered_at", ""),
        "updated_at": getattr(record, "updated_at", ""),
    }


def _agent_payload_matches_status(agent: dict[str, Any], status: str) -> bool:
    if not status:
        return True
    normalized = status.strip().lower()
    if not normalized:
        return True
    state = str(agent.get("state") or agent.get("status") or "").strip().lower()
    if state == normalized:
        return True
    value = agent.get(normalized)
    return bool(value) if isinstance(value, bool) else False


def _agents_from_runtime_query(ctx: RuntimeContext) -> list[dict[str, Any]] | None:
    query = getattr(ctx, "agent_query", None)
    if query is None:
        return None
    try:
        raw_agents = query()
    except (RuntimeError, TypeError, ValueError) as exc:
        raise ToolRuntimeError(
            "EXEC_ERROR",
            f"Failed to query runtime agent discovery snapshot: {exc}",
            {"reason_code": "agent_query_exec_error"},
        ) from exc
    agents: list[dict[str, Any]] = []
    for raw in raw_agents or []:
        if isinstance(raw, dict):
            agents.append(dict(raw))
    return agents


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
    effective_limit = max(1, min(int(validated.limit), 200))
    status_filter = validated.status.strip()
    runtime_agents = _agents_from_runtime_query(ctx)
    if runtime_agents is not None:
        agents = [
            agent
            for agent in runtime_agents
            if _agent_payload_matches_status(agent, status_filter)
        ][:effective_limit]
        return {
            "ok": True,
            "agents": agents,
            "count": len(agents),
            "limit": effective_limit,
            "source": "runtime_agent_discovery",
        }

    registry = _resolve_agent_registry(ctx)
    if registry is None:
        return {
            "ok": True,
            "agents": [],
            "count": 0,
            "limit": int(validated.limit),
            "storage_unavailable": True,
            "source": "registry_compatibility_fallback",
        }
    try:
        rows = registry.list_agents(status=status_filter or None)
    except Exception as exc:
        raise ToolRuntimeError(
            "EXEC_ERROR",
            f"Failed to list agents: {exc}",
            {"reason_code": "agent_registry_exec_error"},
        ) from exc
    agents = [_agent_record_to_dict(row) for row in rows[:effective_limit]]
    return {
        "ok": True,
        "agents": agents,
        "count": len(agents),
        "limit": effective_limit,
        "source": "registry_compatibility_fallback",
    }


def _h_agent_get(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = AgentGetArgs.model_validate(args)
    agent_id = validated.agent_id.strip()
    runtime_agents = _agents_from_runtime_query(ctx)
    if runtime_agents is not None:
        for agent in runtime_agents:
            if str(agent.get("agent_id", "") or "").strip() == agent_id:
                return {
                    "ok": True,
                    "agent": agent,
                    "source": "runtime_agent_discovery",
                }
        raise ToolRuntimeError(
            "NOT_FOUND",
            f"Agent {agent_id!r} is not visible in the current runtime",
            {"reason_code": "agent_not_found", "agent_id": agent_id},
        )

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
        "source": "registry_compatibility_fallback",
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
    if code in _A2A_NOT_FOUND_CODES or code in {
        "A2A_JOB_NOT_FOUND",
        "A2A_JOB_POLL_FAILED",
        "A2A_JOB_CANCEL_FAILED",
    }:
        return "NOT_FOUND"
    if code == "TASK_DELEGATE_INVALID_ARGS":
        return "INVALID_ARGUMENT"
    if str(status or "").strip() == "running":
        return "EXEC_ERROR"
    return "UPSTREAM_ERROR"


def _h_task_delegate(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = TaskDelegateArgs.model_validate(args)
    if validated.mode in {"accept", "reject"}:
        return _handle_child_artifact_disposition(validated, ctx)

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

    if validated.mode == "status":
        result = seam.status(task_id=validated.task_id)
    elif validated.mode == "resume":
        result = seam.resume(task_id=validated.task_id)
    elif validated.mode == "cancel":
        result = seam.cancel(task_id=validated.task_id)
    else:
        policy = getattr(ctx, "policy", None)
        context_metadata = getattr(policy, "raw", {}).get("context_metadata", {})
        if not isinstance(context_metadata, dict):
            context_metadata = {}
        bind_observability = getattr(seam, "bind_observability", None)
        if callable(bind_observability):
            bind_observability(
                session_id=str(getattr(ctx, "telemetry_session_id", "") or ""),
                turn_id=str(getattr(ctx, "telemetry_turn_id", "") or ""),
                invocation_id=str(context_metadata.get("invocation_id") or ""),
                execution_id=str(context_metadata.get("execution_id") or ""),
                traceparent=str(context_metadata.get("traceparent") or ""),
                tracestate=str(context_metadata.get("tracestate") or ""),
            )
        delegate_kwargs = {
            "agent_id": validated.agent_id,
            "instruction": validated.instruction,
            "timeout_seconds": int(validated.timeout_seconds),
            "permission_mode": str(getattr(ctx, "permission_mode", "ask") or "ask"),
            "workspace_root": str(getattr(ctx, "workspace", "") or "").strip(),
            "cwd": str(getattr(ctx, "workspace", "") or "").strip(),
        }
        if validated.mode != "sync":
            delegate_kwargs["mode"] = validated.mode
        result = seam.delegate(**delegate_kwargs)

    if result.ok:
        return {
            "ok": True,
            "agent_id": result.target_agent_id or validated.agent_id,
            "mode": validated.mode,
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


def _handle_child_artifact_disposition(
    args: TaskDelegateArgs, ctx: RuntimeContext
) -> dict[str, Any]:
    artifactctl = getattr(ctx, "artifactctl", None)
    if artifactctl is None:
        raise ToolRuntimeError(
            "DEPENDENCY_MISSING",
            "Child artifact disposition requires ArtifactCtl.",
            {"reason_code": "artifactctl_unavailable"},
        )
    if args.mode == "reject":
        result = reject_child_worktree_artifact(
            record=dict(args.child_artifact), artifactctl=artifactctl
        )
        return {"ok": True, "mode": args.mode, **result}

    result = accept_child_worktree_artifact(
        repo_root=args.workspace_root,
        record=dict(args.child_artifact),
        artifactctl=artifactctl,
    )
    if result.get("ok") is True:
        return {"ok": True, "mode": args.mode, **result}
    raise ToolRuntimeError(
        "POLICY_DENIED",
        "Child artifact acceptance was blocked.",
        {"reason_code": str(result.get("status") or "accept_blocked"), **result},
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
