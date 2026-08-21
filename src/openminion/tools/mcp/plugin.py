"""MCP tool plugin."""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Any, Callable

from openminion.base.config.parse import split_comma_tokens
from openminion.base.config.mcp import MCPApprovalConfig
from openminion.modules.tool.contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry, ToolSpec

from .interfaces import MCPFleetHandle, require_mcp_tool_registration_state
from .results import MCPCallError
from .transport import (
    MCPAuthorizationError,
    MCPProtocolError,
    MCPRemoteTransportError,
    MCPServerUnavailableError,
    MCPTimeoutError,
    MCPTransportError,
)
from .schemas import (
    MCPArgumentValidationError,
    MCPListedPrompt,
    MCPListedResource,
    MCPListedResourceTemplate,
    MCPListedTool,
    build_mcp_runtime_binding_id,
    build_mcp_runtime_prompt_name,
    build_mcp_runtime_resource_name,
    build_mcp_runtime_resource_template_name,
    build_mcp_runtime_tool_name,
    build_supported_parameters_schema,
    render_mcp_resource_template_uri,
    validate_mcp_arguments,
)

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext
    from openminion.modules.tool.runtime.context import RuntimeContext


def _validated_arguments(
    *, schema: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    try:
        return validate_mcp_arguments(schema=schema, arguments=arguments)
    except MCPArgumentValidationError as exc:
        raise ToolRuntimeError("INVALID_ARGUMENT", str(exc)) from exc


def _invoke_mcp(
    *,
    invoke: Callable[[], dict[str, Any]],
    kind: str,
    server_name: str,
    runtime_tool_name: str,
    remote_detail: tuple[str, str],
) -> dict[str, Any]:
    detail_name, detail_value = remote_detail
    details = {
        "mcp_server": server_name,
        "runtime_tool_name": runtime_tool_name,
        detail_name: detail_value,
    }
    try:
        return invoke()
    except MCPServerUnavailableError as exc:
        raise ToolRuntimeError(
            "UPSTREAM_ERROR",
            f"MCP server '{server_name}' is unavailable.",
            details=_mcp_error_details(
                exc,
                default_reason_code="mcp_server_unavailable",
                **details,
            ),
        ) from exc
    except MCPTimeoutError as exc:
        raise ToolRuntimeError(
            "TIMEOUT",
            f"MCP {kind} '{runtime_tool_name}' timed out.",
            details={"reason_code": f"mcp_{kind}_timeout", **details},
        ) from exc
    except MCPAuthorizationError as exc:
        authorization_details: dict[str, Any] = {
            "status_code": exc.status_code,
            "www_authenticate": exc.www_authenticate,
        }
        if exc.auth_challenge:
            authorization_details["auth_challenge"] = dict(exc.auth_challenge)
        raise ToolRuntimeError(
            "UPSTREAM_ERROR",
            str(exc),
            details=_mcp_error_details(
                exc,
                default_reason_code="mcp_authorization_error",
                **authorization_details,
                **details,
            ),
        ) from exc
    except (MCPProtocolError, MCPRemoteTransportError, MCPCallError) as exc:
        raise ToolRuntimeError(
            "UPSTREAM_ERROR",
            str(exc),
            details=_mcp_error_details(
                exc,
                default_reason_code="mcp_upstream_error",
                **details,
            ),
        ) from exc


def build_mcp_tool_spec(
    *,
    manager: MCPFleetHandle,
    tool: MCPListedTool,
) -> ToolSpec:
    runtime_tool_name = build_mcp_runtime_tool_name(
        server_name=tool.server_name,
        remote_name=tool.remote_name,
    )
    parameters_schema = build_supported_parameters_schema(tool.input_schema)

    def _handler(
        arguments: dict[str, Any], _runtime_ctx: RuntimeContext
    ) -> dict[str, Any]:
        _enforce_mcp_approval(
            manager=manager,
            tool=tool,
            runtime_tool_name=runtime_tool_name,
            runtime_ctx=_runtime_ctx,
        )
        validated = _validated_arguments(
            schema=tool.input_schema,
            arguments=arguments,
        )
        return _invoke_mcp(
            invoke=lambda: manager.call_tool(
                server_name=tool.server_name,
                remote_name=tool.remote_name,
                arguments=validated,
            ),
            kind="tool",
            server_name=tool.server_name,
            runtime_tool_name=runtime_tool_name,
            remote_detail=("mcp_remote_tool_name", tool.remote_name),
        )

    return ToolSpec(
        name=runtime_tool_name,
        args_model=dict,
        min_scope=tool.posture.min_scope,
        handler=_handler,
        dangerous=tool.posture.dangerous,
        idempotent=tool.posture.idempotent,
        tags=("mcp", tool.server_name),
        capabilities=("mcp", tool.server_name, tool.remote_name),
        parameters_schema=parameters_schema,
        prompt_visible_runtime_name=True,
        runtime_binding_id=build_mcp_runtime_binding_id(
            runtime_tool_name=runtime_tool_name
        ),
    )


def _enforce_mcp_approval(
    *,
    manager: MCPFleetHandle,
    tool: MCPListedTool,
    runtime_tool_name: str,
    runtime_ctx: RuntimeContext,
) -> None:
    server_config = manager.server_config(tool.server_name)
    if server_config is None or not _mcp_approval_required(
        approval=server_config.approval,
        tool=tool,
        runtime_tool_name=runtime_tool_name,
    ):
        return
    approval = server_config.approval
    candidate = runtime_ctx.policy.raw.get("context_metadata", {})
    context_metadata = candidate if isinstance(candidate, dict) else {}
    if _mcp_approval_granted(
        metadata=context_metadata,
        server_name=tool.server_name,
        remote_name=tool.remote_name,
        runtime_tool_name=runtime_tool_name,
    ):
        return
    raise ToolRuntimeError(
        TOOL_ERROR_CONFIRM_REQUIRED,
        f"MCP tool '{runtime_tool_name}' requires approval before remote execution.",
        details={
            "reason_code": "POLICY_MCP_APPROVAL_REQUIRED",
            "approval_required": True,
            "requires_confirm": True,
            "approval_mode": approval.mode,
            "mcp_server": tool.server_name,
            "mcp_remote_tool_name": tool.remote_name,
            "runtime_tool_name": runtime_tool_name,
        },
    )


def _mcp_approval_required(
    *,
    approval: MCPApprovalConfig,
    tool: MCPListedTool,
    runtime_tool_name: str,
) -> bool:
    mode = approval.mode
    if mode == "never":
        return False
    if mode == "always":
        return True
    risk = _mcp_tool_risk(tool)
    if mode == "dangerous":
        return risk in {"high", "critical"}
    if mode == "matching":
        patterns = tuple(approval.tool_patterns)
        risk_levels = set(approval.risk_levels)
        pattern_match = any(
            fnmatch.fnmatch(runtime_tool_name, pattern)
            or fnmatch.fnmatch(tool.remote_name, pattern)
            for pattern in patterns
        )
        return pattern_match or (bool(risk_levels) and risk in risk_levels)
    return False


def _mcp_tool_risk(tool: MCPListedTool) -> str:
    if tool.posture.dangerous:
        return "high"
    min_scope = tool.posture.min_scope
    if min_scope in {"POWER_USER", "UI_AUTOMATION"}:
        return "high"
    if min_scope == "WRITE_SAFE":
        return "medium"
    return "low"


def _mcp_approval_granted(
    *,
    metadata: dict[str, Any],
    server_name: str,
    remote_name: str,
    runtime_tool_name: str,
) -> bool:
    if (
        str(metadata.get("confirmation_source", "") or "").strip() == "policy_replay"
        and str(metadata.get("confirmation_grant_id", "") or "").strip()
    ):
        return True
    decision = str(metadata.get("mcp_approval", "") or "").strip().lower()
    if decision in {"deny", "denied"}:
        return False
    approved_tools = _metadata_csv(metadata.get("mcp_approved_runtime_tools"))
    approved_servers = _metadata_csv(metadata.get("mcp_approved_servers"))
    approved_remote_tools = _metadata_csv(metadata.get("mcp_approved_remote_tools"))
    if "*" in approved_tools or runtime_tool_name in approved_tools:
        return True
    if "*" in approved_servers or server_name in approved_servers:
        return True
    if "*" in approved_remote_tools or remote_name in approved_remote_tools:
        return True
    approved_tool = str(metadata.get("mcp_approval_tool", "") or "").strip()
    if decision in {"approve", "approved", "allow", "allowed", "allow_once"}:
        return not approved_tool or approved_tool in {
            runtime_tool_name,
            remote_name,
            server_name,
        }
    return False


def _metadata_csv(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(item or "").strip() for item in value if str(item or "").strip()}
    return set(split_comma_tokens(value))


def build_mcp_prompt_spec(
    *,
    manager: MCPFleetHandle,
    prompt: MCPListedPrompt,
) -> ToolSpec:
    runtime_tool_name = build_mcp_runtime_prompt_name(
        server_name=prompt.server_name,
        remote_name=prompt.remote_name,
    )
    parameters_schema = build_supported_parameters_schema(prompt.arguments_schema)

    def _handler(
        arguments: dict[str, Any], _runtime_ctx: RuntimeContext
    ) -> dict[str, Any]:
        del _runtime_ctx
        validated = _validated_arguments(
            schema=prompt.arguments_schema,
            arguments=arguments,
        )
        return _invoke_mcp(
            invoke=lambda: manager.get_prompt(
                server_name=prompt.server_name,
                remote_name=prompt.remote_name,
                arguments=validated,
            ),
            kind="prompt",
            server_name=prompt.server_name,
            runtime_tool_name=runtime_tool_name,
            remote_detail=("mcp_remote_prompt_name", prompt.remote_name),
        )

    return ToolSpec(
        name=runtime_tool_name,
        args_model=dict,
        min_scope="READ_ONLY",
        handler=_handler,
        dangerous=False,
        idempotent=True,
        tags=("mcp", "prompt", prompt.server_name),
        capabilities=("mcp", "prompt", prompt.server_name, prompt.remote_name),
        parameters_schema=parameters_schema,
        prompt_visible_runtime_name=True,
        runtime_binding_id=build_mcp_runtime_binding_id(
            runtime_tool_name=runtime_tool_name
        ),
    )


def build_mcp_resource_spec(
    *,
    manager: MCPFleetHandle,
    resource: MCPListedResource,
) -> ToolSpec:
    runtime_tool_name = build_mcp_runtime_resource_name(
        server_name=resource.server_name,
        resource_uri=resource.resource_uri,
        resource_name=resource.resource_name,
    )
    parameters_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def _handler(
        arguments: dict[str, Any], _runtime_ctx: RuntimeContext
    ) -> dict[str, Any]:
        del _runtime_ctx
        _validated_arguments(schema=parameters_schema, arguments=arguments)
        return _invoke_mcp(
            invoke=lambda: manager.read_resource(
                server_name=resource.server_name,
                resource_uri=resource.resource_uri,
            ),
            kind="resource",
            server_name=resource.server_name,
            runtime_tool_name=runtime_tool_name,
            remote_detail=("mcp_resource_uri", resource.resource_uri),
        )

    return ToolSpec(
        name=runtime_tool_name,
        args_model=dict,
        min_scope="READ_ONLY",
        handler=_handler,
        dangerous=False,
        idempotent=True,
        tags=("mcp", "resource", resource.server_name),
        capabilities=("mcp", "resource", resource.server_name, resource.resource_uri),
        parameters_schema=parameters_schema,
        prompt_visible_runtime_name=True,
        runtime_binding_id=build_mcp_runtime_binding_id(
            runtime_tool_name=runtime_tool_name
        ),
    )


def build_mcp_resource_template_spec(
    *,
    manager: MCPFleetHandle,
    template: MCPListedResourceTemplate,
) -> ToolSpec:
    runtime_tool_name = build_mcp_runtime_resource_template_name(
        server_name=template.server_name,
        uri_template=template.uri_template,
        template_name=template.template_name,
    )
    parameters_schema = build_supported_parameters_schema(template.arguments_schema)

    def _handler(
        arguments: dict[str, Any], _runtime_ctx: RuntimeContext
    ) -> dict[str, Any]:
        del _runtime_ctx
        validated = _validated_arguments(
            schema=template.arguments_schema,
            arguments=arguments,
        )
        resource_uri = render_mcp_resource_template_uri(
            uri_template=template.uri_template,
            arguments=validated,
        )
        return _invoke_mcp(
            invoke=lambda: manager.read_resource(
                server_name=template.server_name,
                resource_uri=resource_uri,
            ),
            kind="resource_template",
            server_name=template.server_name,
            runtime_tool_name=runtime_tool_name,
            remote_detail=("mcp_resource_template", template.uri_template),
        )

    return ToolSpec(
        name=runtime_tool_name,
        args_model=dict,
        min_scope="READ_ONLY",
        handler=_handler,
        dangerous=False,
        idempotent=True,
        tags=("mcp", "resource_template", template.server_name),
        capabilities=(
            "mcp",
            "resource_template",
            template.server_name,
            template.uri_template,
        ),
        parameters_schema=parameters_schema,
        prompt_visible_runtime_name=True,
        runtime_binding_id=build_mcp_runtime_binding_id(
            runtime_tool_name=runtime_tool_name
        ),
    )


def describe_mcp_tool(
    *,
    tool: MCPListedTool,
) -> tuple[str, str]:
    runtime_tool_name = build_mcp_runtime_tool_name(
        server_name=tool.server_name,
        remote_name=tool.remote_name,
    )
    return _describe_runtime_tool_name(runtime_tool_name)


def describe_mcp_prompt(
    *,
    prompt: MCPListedPrompt,
) -> tuple[str, str]:
    runtime_tool_name = build_mcp_runtime_prompt_name(
        server_name=prompt.server_name,
        remote_name=prompt.remote_name,
    )
    return _describe_runtime_tool_name(runtime_tool_name)


def describe_mcp_resource(
    *,
    resource: MCPListedResource,
) -> tuple[str, str]:
    runtime_tool_name = build_mcp_runtime_resource_name(
        server_name=resource.server_name,
        resource_uri=resource.resource_uri,
        resource_name=resource.resource_name,
    )
    return _describe_runtime_tool_name(runtime_tool_name)


def describe_mcp_resource_template(
    *,
    template: MCPListedResourceTemplate,
) -> tuple[str, str]:
    runtime_tool_name = build_mcp_runtime_resource_template_name(
        server_name=template.server_name,
        uri_template=template.uri_template,
        template_name=template.template_name,
    )
    return _describe_runtime_tool_name(runtime_tool_name)


def _describe_runtime_tool_name(runtime_tool_name: str) -> tuple[str, str]:
    return runtime_tool_name, build_mcp_runtime_binding_id(
        runtime_tool_name=runtime_tool_name
    )


def register(registry: ToolRegistry, ctx: ToolRegisterContext | None = None) -> None:
    state = require_mcp_tool_registration_state(
        ctx.prepared_state if ctx is not None else None
    )
    for tool in state.supported_tools:
        registry.register(build_mcp_tool_spec(manager=state.manager, tool=tool))
    for prompt in state.supported_prompts:
        registry.register(build_mcp_prompt_spec(manager=state.manager, prompt=prompt))
    for resource in state.supported_resources:
        registry.register(
            build_mcp_resource_spec(manager=state.manager, resource=resource)
        )
    for template in state.supported_resource_templates:
        registry.register(
            build_mcp_resource_template_spec(
                manager=state.manager,
                template=template,
            )
        )


def _mcp_error_details(
    exc: MCPTransportError | MCPCallError,
    *,
    default_reason_code: str,
    **extra: Any,
) -> dict[str, Any]:
    details = dict(exc.details)
    details.setdefault(
        "reason_code",
        exc.reason_code or default_reason_code,
    )
    details.update(extra)
    return details


__all__ = [
    "MCPArgumentValidationError",
    "build_mcp_prompt_spec",
    "build_mcp_resource_spec",
    "build_mcp_resource_template_spec",
    "build_mcp_tool_spec",
    "describe_mcp_prompt",
    "describe_mcp_resource",
    "describe_mcp_resource_template",
    "describe_mcp_tool",
    "register",
]
