from typing import Any

from openminion.modules.tool.authoring.schemas import (
    ToolAuthorArgs,
    ToolGetArgs,
    ToolInspectArgs,
    ToolRegisterArgs,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_TOOL_AUTHOR,
    MODEL_TOOL_GET,
    MODEL_TOOL_INSPECT,
    MODEL_TOOL_REGISTER,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_TOOL_AUTHOR,
    RUNTIME_TOOL_GET,
    RUNTIME_TOOL_INSPECT,
    RUNTIME_TOOL_REGISTER,
)
from openminion.modules.tool.registry import ToolRegistry, ToolSpec


def _require_authored_tools_api(ctx: Any) -> Any:
    service = getattr(ctx, "authored_tools_api", None)
    if service is None:
        return None
    return service


def _service_unavailable() -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "AUTHORED_TOOLS_UNAVAILABLE",
            "message": "Authored tool service is not available in this runtime context.",
        },
    }


def _h_tool_author(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    service = _require_authored_tools_api(ctx)
    if service is None:
        return _service_unavailable()
    return service.author_draft(
        args,
        agent_id=getattr(ctx, "agent_id", None),
        session_id=getattr(ctx, "session_id", None),
    )


def _h_tool_inspect(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    service = _require_authored_tools_api(ctx)
    if service is None:
        return _service_unavailable()
    return service.inspect_draft(
        args,
        agent_id=getattr(ctx, "agent_id", None),
        session_id=getattr(ctx, "session_id", None),
    )


def _h_tool_register(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    service = _require_authored_tools_api(ctx)
    if service is None:
        return _service_unavailable()
    return service.register_draft(
        args,
        agent_id=getattr(ctx, "agent_id", None),
        session_id=getattr(ctx, "session_id", None),
    )


def _h_tool_get(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    service = _require_authored_tools_api(ctx)
    if service is None:
        return _service_unavailable()
    detail = service.get_authored_tool_detail(str(args["tool_name"]))
    if detail is None:
        return {
            "ok": False,
            "error": {
                "code": "TOOL_NOT_FOUND",
                "message": str(args["tool_name"]),
            },
        }
    return {"ok": True, "tool": detail}


def register(registry: ToolRegistry) -> None:
    registry.add(
        ToolSpec(
            name=MODEL_TOOL_AUTHOR,
            args_model=ToolAuthorArgs,
            min_scope="POWER_USER",
            handler=_h_tool_author,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "tool_authoring", "write"),
            capabilities=("tool_authoring", "write"),
            runtime_binding_id=RUNTIME_TOOL_AUTHOR,
            block_under_readonly=True,
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_TOOL_INSPECT,
            args_model=ToolInspectArgs,
            min_scope="POWER_USER",
            handler=_h_tool_inspect,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "tool_authoring", "inspect"),
            capabilities=("tool_authoring", "inspect"),
            runtime_binding_id=RUNTIME_TOOL_INSPECT,
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_TOOL_REGISTER,
            args_model=ToolRegisterArgs,
            min_scope="POWER_USER",
            handler=_h_tool_register,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "tool_authoring", "write"),
            capabilities=("tool_authoring", "write"),
            runtime_binding_id=RUNTIME_TOOL_REGISTER,
            block_under_readonly=True,
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_TOOL_GET,
            args_model=ToolGetArgs,
            min_scope="READ_ONLY",
            handler=_h_tool_get,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "tool_authoring", "read"),
            capabilities=("tool_authoring", "read"),
            runtime_binding_id=RUNTIME_TOOL_GET,
        )
    )


__all__ = ["register"]
