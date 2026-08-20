"""MCP tool schemas."""

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from openminion.base.config.mcp import normalize_mcp_tool_segment


@dataclass(frozen=True)
class MCPToolPosture:
    min_scope: str = "WRITE_SAFE"
    dangerous: bool = False
    idempotent: bool = False


@dataclass(frozen=True)
class MCPCompletionResult:
    values: tuple[str, ...] = ()
    total: int | None = None
    has_more: bool = False


@dataclass(frozen=True)
class MCPListedTool:
    server_name: str
    remote_name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)
    posture: MCPToolPosture = field(default_factory=MCPToolPosture)
    output_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPListedPrompt:
    server_name: str
    remote_name: str
    description: str
    arguments_schema: dict[str, Any]


@dataclass(frozen=True)
class MCPListedResource:
    server_name: str
    resource_uri: str
    resource_name: str
    description: str
    mime_type: str


@dataclass(frozen=True)
class MCPListedResourceTemplate:
    server_name: str
    uri_template: str
    template_name: str
    description: str
    mime_type: str
    arguments_schema: dict[str, Any]


@dataclass(frozen=True)
class MCPLogMessage:
    level: str
    message: str
    logger: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0


@dataclass(frozen=True)
class MCPResourceUpdate:
    server_name: str
    uri: str
    title: str = ""
    timestamp: float = 0.0


@dataclass(frozen=True)
class MCPRoot:
    uri: str
    name: str = ""


@dataclass(frozen=True)
class MCPSamplingMessage:
    role: str
    content: Any


@dataclass(frozen=True)
class MCPSamplingRequest:
    messages: tuple[MCPSamplingMessage, ...]
    max_tokens: int | None = None
    system_prompt: str = ""
    model_preferences: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPSamplingResult:
    role: str = "assistant"
    content: Any = field(default_factory=dict)
    model: str = ""
    stop_reason: str = ""


@dataclass(frozen=True)
class MCPElicitationRequest:
    mode: str
    message: str
    requested_schema: dict[str, Any] = field(default_factory=dict)
    url: str = ""
    elicitation_id: str = ""
    raw_params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPElicitationResult:
    action: str
    content: dict[str, Any] | None = None


@dataclass(frozen=True)
class MCPPreparedSchema:
    mode: str
    parameters_schema: dict[str, Any]
    note: str = ""


class MCPUnsupportedSchemaError(RuntimeError):
    """Raised when an MCP tool schema is outside the supported subset."""


class MCPArgumentValidationError(RuntimeError):
    """Raised when MCP tool arguments do not match the supported schema subset."""


_RESOURCE_TEMPLATE_VARIABLE_RE = re.compile(r"{([A-Za-z_][A-Za-z0-9_]*)}")


def build_mcp_runtime_tool_name(*, server_name: str, remote_name: str) -> str:
    return f"mcp.{server_name}.{normalize_mcp_tool_segment(remote_name)}"


def build_mcp_runtime_prompt_name(*, server_name: str, remote_name: str) -> str:
    return f"mcp.{server_name}.prompt.{normalize_mcp_tool_segment(remote_name)}"


def build_mcp_runtime_resource_name(
    *,
    server_name: str,
    resource_uri: str,
    resource_name: str = "",
) -> str:
    display_token = resource_name.strip() or resource_uri.strip()
    return f"mcp.{server_name}.resource.{normalize_mcp_tool_segment(display_token)}"


def build_mcp_runtime_resource_template_name(
    *,
    server_name: str,
    uri_template: str,
    template_name: str = "",
) -> str:
    display_token = template_name.strip() or uri_template.strip()
    return (
        f"mcp.{server_name}.resource_template."
        f"{normalize_mcp_tool_segment(display_token)}"
    )


def build_mcp_runtime_binding_id(*, runtime_tool_name: str) -> str:
    token = runtime_tool_name.strip()
    return f"runtime.{token}" if token else ""


def prepare_mcp_registration_schema(
    input_schema: Mapping[str, Any] | None,
) -> MCPPreparedSchema:
    schema = dict(input_schema or {})
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise MCPUnsupportedSchemaError(
            f"MCP input schema is not valid JSON Schema 2020-12: {exc.message}"
        ) from exc
    return MCPPreparedSchema(
        mode="strict",
        parameters_schema=copy.deepcopy(schema),
    )


def build_supported_parameters_schema(
    input_schema: Mapping[str, Any] | None,
) -> dict[str, Any]:
    prepared = prepare_mcp_registration_schema(input_schema)
    return copy.deepcopy(prepared.parameters_schema)


def build_mcp_resource_template_arguments_schema(uri_template: str) -> dict[str, Any]:
    variables = tuple(
        dict.fromkeys(_RESOURCE_TEMPLATE_VARIABLE_RE.findall(uri_template))
    )
    return {
        "type": "object",
        "properties": {
            variable: {
                "type": "string",
                "description": f"Value for {{{variable}}} in the MCP resource URI.",
            }
            for variable in variables
        },
        "required": list(variables),
        "additionalProperties": False,
    }


def validate_mcp_arguments(
    *,
    schema: Mapping[str, Any] | None,
    arguments: Mapping[str, Any] | None,
) -> dict[str, Any]:
    input_schema = dict(schema or {})
    prepare_mcp_registration_schema(input_schema)
    if arguments is not None and not isinstance(arguments, Mapping):
        raise MCPArgumentValidationError("arguments must be an object.")
    value = dict(arguments or {})
    try:
        Draft202012Validator(input_schema).validate(value)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path)
        prefix = f"arguments.{location}" if location else "arguments"
        raise MCPArgumentValidationError(f"{prefix}: {exc.message}") from exc
    return value


def render_mcp_resource_template_uri(
    *,
    uri_template: str,
    arguments: Mapping[str, Any],
) -> str:
    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(arguments.get(key, "") or "")

    return _RESOURCE_TEMPLATE_VARIABLE_RE.sub(_replace, uri_template)


__all__ = [
    "MCPArgumentValidationError",
    "MCPCompletionResult",
    "MCPElicitationRequest",
    "MCPElicitationResult",
    "MCPListedPrompt",
    "MCPListedResource",
    "MCPListedResourceTemplate",
    "MCPListedTool",
    "MCPLogMessage",
    "MCPPreparedSchema",
    "MCPResourceUpdate",
    "MCPRoot",
    "MCPSamplingMessage",
    "MCPSamplingRequest",
    "MCPSamplingResult",
    "MCPToolPosture",
    "MCPUnsupportedSchemaError",
    "build_mcp_runtime_binding_id",
    "build_mcp_runtime_prompt_name",
    "build_mcp_runtime_resource_name",
    "build_mcp_runtime_resource_template_name",
    "build_mcp_runtime_tool_name",
    "build_mcp_resource_template_arguments_schema",
    "build_supported_parameters_schema",
    "prepare_mcp_registration_schema",
    "render_mcp_resource_template_uri",
    "validate_mcp_arguments",
]
