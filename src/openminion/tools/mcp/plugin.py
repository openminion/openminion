"""MCP tool plugin."""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING
from typing import Any

from openminion.modules.tool.contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry, ToolSpec

from .interfaces import MCPFleetHandle, require_mcp_tool_registration_state
from .manager import (
    MCPAuthorizationError,
    MCPCallError,
    MCPProtocolError,
    MCPRemoteTransportError,
    MCPServerUnavailableError,
    MCPTimeoutError,
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

    def _handler(arguments: dict[str, Any], _runtime_ctx: Any) -> dict[str, Any]:
        try:
            _enforce_mcp_approval(
                manager=manager,
                tool=tool,
                runtime_tool_name=runtime_tool_name,
                runtime_ctx=_runtime_ctx,
            )
            validated = validate_mcp_arguments(
                schema=tool.input_schema,
                arguments=arguments,
            )
            return manager.call_tool(
                server_name=tool.server_name,
                remote_name=tool.remote_name,
                arguments=validated,
            )
        except MCPArgumentValidationError as exc:
            raise ToolRuntimeError("INVALID_ARGUMENT", str(exc)) from exc
        except MCPServerUnavailableError as exc:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                f"MCP server '{tool.server_name}' is unavailable.",
                details=_mcp_error_details(
                    exc,
                    default_reason_code="mcp_server_unavailable",
                    mcp_server=tool.server_name,
                    runtime_tool_name=runtime_tool_name,
                    mcp_remote_tool_name=tool.remote_name,
                ),
            ) from exc
        except MCPTimeoutError as exc:
            raise ToolRuntimeError(
                "TIMEOUT",
                f"MCP tool '{runtime_tool_name}' timed out.",
                details={
                    "reason_code": "mcp_tool_timeout",
                    "mcp_server": tool.server_name,
                    "mcp_remote_tool_name": tool.remote_name,
                },
            ) from exc
        except MCPAuthorizationError as exc:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                str(exc),
                details=_mcp_error_details(
                    exc,
                    default_reason_code="mcp_authorization_error",
                    mcp_server=tool.server_name,
                    runtime_tool_name=runtime_tool_name,
                    mcp_remote_tool_name=tool.remote_name,
                    status_code=exc.status_code,
                    www_authenticate=exc.www_authenticate,
                ),
            ) from exc
        except (MCPProtocolError, MCPRemoteTransportError, MCPCallError) as exc:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                str(exc),
                details=_mcp_error_details(
                    exc,
                    default_reason_code="mcp_upstream_error",
                    mcp_server=tool.server_name,
                    runtime_tool_name=runtime_tool_name,
                    mcp_remote_tool_name=tool.remote_name,
                ),
            ) from exc

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
    runtime_ctx: Any,
) -> None:
    server_config_getter = getattr(manager, "server_config", None)
    server_config = (
        server_config_getter(tool.server_name)
        if callable(server_config_getter)
        else None
    )
    approval = getattr(server_config, "approval", None)
    if approval is None or not _mcp_approval_required(
        approval=approval,
        tool=tool,
        runtime_tool_name=runtime_tool_name,
    ):
        return
    metadata = getattr(getattr(runtime_ctx, "policy", None), "raw", {})
    context_metadata = {}
    if isinstance(metadata, dict):
        candidate = metadata.get("context_metadata", {})
        if isinstance(candidate, dict):
            context_metadata = candidate
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
            "approval_mode": str(getattr(approval, "mode", "") or ""),
            "mcp_server": tool.server_name,
            "mcp_remote_tool_name": tool.remote_name,
            "runtime_tool_name": runtime_tool_name,
        },
    )


def _mcp_approval_required(
    *,
    approval: Any,
    tool: MCPListedTool,
    runtime_tool_name: str,
) -> bool:
    mode = str(getattr(approval, "mode", "never") or "never").strip().lower()
    if mode == "never":
        return False
    if mode == "always":
        return True
    risk = _mcp_tool_risk(tool)
    if mode == "dangerous":
        return risk in {"high", "critical"}
    if mode == "matching":
        patterns = tuple(getattr(approval, "tool_patterns", ()) or ())
        risk_levels = {
            str(item or "").strip().lower()
            for item in (getattr(approval, "risk_levels", ()) or ())
            if str(item or "").strip()
        }
        pattern_match = any(
            fnmatch.fnmatch(runtime_tool_name, pattern)
            or fnmatch.fnmatch(tool.remote_name, pattern)
            for pattern in patterns
        )
        return pattern_match or (bool(risk_levels) and risk in risk_levels)
    return False


def _mcp_tool_risk(tool: MCPListedTool) -> str:
    if bool(getattr(tool.posture, "dangerous", False)):
        return "high"
    min_scope = str(getattr(tool.posture, "min_scope", "") or "").strip().upper()
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
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


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

    def _handler(arguments: dict[str, Any], _runtime_ctx: Any) -> dict[str, Any]:
        try:
            validated = validate_mcp_arguments(
                schema=prompt.arguments_schema,
                arguments=arguments,
            )
            return manager.get_prompt(
                server_name=prompt.server_name,
                remote_name=prompt.remote_name,
                arguments=validated,
            )
        except MCPArgumentValidationError as exc:
            raise ToolRuntimeError("INVALID_ARGUMENT", str(exc)) from exc
        except MCPServerUnavailableError as exc:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                f"MCP server '{prompt.server_name}' is unavailable.",
                details=_mcp_error_details(
                    exc,
                    default_reason_code="mcp_server_unavailable",
                    mcp_server=prompt.server_name,
                    runtime_tool_name=runtime_tool_name,
                    mcp_remote_prompt_name=prompt.remote_name,
                ),
            ) from exc
        except MCPTimeoutError as exc:
            raise ToolRuntimeError(
                "TIMEOUT",
                f"MCP prompt '{runtime_tool_name}' timed out.",
                details={
                    "reason_code": "mcp_prompt_timeout",
                    "mcp_server": prompt.server_name,
                    "mcp_remote_prompt_name": prompt.remote_name,
                },
            ) from exc
        except MCPAuthorizationError as exc:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                str(exc),
                details=_mcp_error_details(
                    exc,
                    default_reason_code="mcp_authorization_error",
                    mcp_server=prompt.server_name,
                    runtime_tool_name=runtime_tool_name,
                    mcp_remote_prompt_name=prompt.remote_name,
                    status_code=exc.status_code,
                    www_authenticate=exc.www_authenticate,
                ),
            ) from exc
        except (MCPProtocolError, MCPRemoteTransportError, MCPCallError) as exc:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                str(exc),
                details=_mcp_error_details(
                    exc,
                    default_reason_code="mcp_upstream_error",
                    mcp_server=prompt.server_name,
                    runtime_tool_name=runtime_tool_name,
                    mcp_remote_prompt_name=prompt.remote_name,
                ),
            ) from exc

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

    def _handler(arguments: dict[str, Any], _runtime_ctx: Any) -> dict[str, Any]:
        try:
            validated = validate_mcp_arguments(
                schema=parameters_schema,
                arguments=arguments,
            )
            assert isinstance(validated, dict)
            return manager.read_resource(
                server_name=resource.server_name,
                resource_uri=resource.resource_uri,
            )
        except MCPArgumentValidationError as exc:
            raise ToolRuntimeError("INVALID_ARGUMENT", str(exc)) from exc
        except MCPServerUnavailableError as exc:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                f"MCP server '{resource.server_name}' is unavailable.",
                details=_mcp_error_details(
                    exc,
                    default_reason_code="mcp_server_unavailable",
                    mcp_server=resource.server_name,
                    runtime_tool_name=runtime_tool_name,
                    mcp_resource_uri=resource.resource_uri,
                ),
            ) from exc
        except MCPTimeoutError as exc:
            raise ToolRuntimeError(
                "TIMEOUT",
                f"MCP resource '{runtime_tool_name}' timed out.",
                details={
                    "reason_code": "mcp_resource_timeout",
                    "mcp_server": resource.server_name,
                    "mcp_resource_uri": resource.resource_uri,
                },
            ) from exc
        except MCPAuthorizationError as exc:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                str(exc),
                details=_mcp_error_details(
                    exc,
                    default_reason_code="mcp_authorization_error",
                    mcp_server=resource.server_name,
                    runtime_tool_name=runtime_tool_name,
                    mcp_resource_uri=resource.resource_uri,
                    status_code=exc.status_code,
                    www_authenticate=exc.www_authenticate,
                ),
            ) from exc
        except (MCPProtocolError, MCPRemoteTransportError, MCPCallError) as exc:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                str(exc),
                details=_mcp_error_details(
                    exc,
                    default_reason_code="mcp_upstream_error",
                    mcp_server=resource.server_name,
                    runtime_tool_name=runtime_tool_name,
                    mcp_resource_uri=resource.resource_uri,
                ),
            ) from exc

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

    def _handler(arguments: dict[str, Any], _runtime_ctx: Any) -> dict[str, Any]:
        try:
            validated = validate_mcp_arguments(
                schema=template.arguments_schema,
                arguments=arguments,
            )
            resource_uri = render_mcp_resource_template_uri(
                uri_template=template.uri_template,
                arguments=validated,
            )
            return manager.read_resource(
                server_name=template.server_name,
                resource_uri=resource_uri,
            )
        except MCPArgumentValidationError as exc:
            raise ToolRuntimeError("INVALID_ARGUMENT", str(exc)) from exc
        except MCPServerUnavailableError as exc:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                f"MCP server '{template.server_name}' is unavailable.",
                details=_mcp_error_details(
                    exc,
                    default_reason_code="mcp_server_unavailable",
                    mcp_server=template.server_name,
                    runtime_tool_name=runtime_tool_name,
                    mcp_resource_template=template.uri_template,
                ),
            ) from exc
        except MCPTimeoutError as exc:
            raise ToolRuntimeError(
                "TIMEOUT",
                f"MCP resource template '{runtime_tool_name}' timed out.",
                details={
                    "reason_code": "mcp_resource_template_timeout",
                    "mcp_server": template.server_name,
                    "mcp_resource_template": template.uri_template,
                },
            ) from exc
        except MCPAuthorizationError as exc:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                str(exc),
                details=_mcp_error_details(
                    exc,
                    default_reason_code="mcp_authorization_error",
                    mcp_server=template.server_name,
                    runtime_tool_name=runtime_tool_name,
                    mcp_resource_template=template.uri_template,
                    status_code=exc.status_code,
                    www_authenticate=exc.www_authenticate,
                ),
            ) from exc
        except (MCPProtocolError, MCPRemoteTransportError, MCPCallError) as exc:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                str(exc),
                details=_mcp_error_details(
                    exc,
                    default_reason_code="mcp_upstream_error",
                    mcp_server=template.server_name,
                    runtime_tool_name=runtime_tool_name,
                    mcp_resource_template=template.uri_template,
                ),
            ) from exc

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
    runtime_binding_id = build_mcp_runtime_binding_id(
        runtime_tool_name=runtime_tool_name
    )
    return runtime_tool_name, runtime_binding_id


def describe_mcp_prompt(
    *,
    prompt: MCPListedPrompt,
) -> tuple[str, str]:
    runtime_tool_name = build_mcp_runtime_prompt_name(
        server_name=prompt.server_name,
        remote_name=prompt.remote_name,
    )
    runtime_binding_id = build_mcp_runtime_binding_id(
        runtime_tool_name=runtime_tool_name
    )
    return runtime_tool_name, runtime_binding_id


def describe_mcp_resource(
    *,
    resource: MCPListedResource,
) -> tuple[str, str]:
    runtime_tool_name = build_mcp_runtime_resource_name(
        server_name=resource.server_name,
        resource_uri=resource.resource_uri,
        resource_name=resource.resource_name,
    )
    runtime_binding_id = build_mcp_runtime_binding_id(
        runtime_tool_name=runtime_tool_name
    )
    return runtime_tool_name, runtime_binding_id


def describe_mcp_resource_template(
    *,
    template: MCPListedResourceTemplate,
) -> tuple[str, str]:
    runtime_tool_name = build_mcp_runtime_resource_template_name(
        server_name=template.server_name,
        uri_template=template.uri_template,
        template_name=template.template_name,
    )
    runtime_binding_id = build_mcp_runtime_binding_id(
        runtime_tool_name=runtime_tool_name
    )
    return runtime_tool_name, runtime_binding_id


def register(registry: ToolRegistry, ctx: ToolRegisterContext | None = None) -> None:
    state = require_mcp_tool_registration_state(
        getattr(ctx, "prepared_state", None) if ctx is not None else None
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
    exc: Exception,
    *,
    default_reason_code: str,
    **extra: Any,
) -> dict[str, Any]:
    details = dict(getattr(exc, "details", {}) or {})
    details.setdefault(
        "reason_code",
        str(getattr(exc, "reason_code", "") or "").strip() or default_reason_code,
    )
    auth_challenge = getattr(exc, "auth_challenge", None)
    if isinstance(auth_challenge, dict) and auth_challenge:
        details["auth_challenge"] = dict(auth_challenge)
    for key, value in extra.items():
        details[key] = value
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
