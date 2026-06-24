"""MCP tool schemas."""

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

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


_UNSUPPORTED_SCHEMA_KEYS = (
    "$ref",
    "oneOf",
    "allOf",
    "not",
    "patternProperties",
)
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
    display_token = str(resource_name or "").strip() or str(resource_uri or "").strip()
    return f"mcp.{server_name}.resource.{normalize_mcp_tool_segment(display_token)}"


def build_mcp_runtime_resource_template_name(
    *,
    server_name: str,
    uri_template: str,
    template_name: str = "",
) -> str:
    display_token = str(template_name or "").strip() or str(uri_template or "").strip()
    return (
        f"mcp.{server_name}.resource_template."
        f"{normalize_mcp_tool_segment(display_token)}"
    )


def build_mcp_runtime_binding_id(*, runtime_tool_name: str) -> str:
    token = str(runtime_tool_name or "").strip()
    return f"runtime.{token}" if token else ""


def prepare_mcp_registration_schema(
    input_schema: Mapping[str, Any] | None,
) -> MCPPreparedSchema:
    schema = dict(input_schema or {})
    mode, note = _classify_schema_support(schema, path="input_schema", root=True)
    if mode == "passthrough":
        return MCPPreparedSchema(
            mode="passthrough",
            parameters_schema=_passthrough_parameters_schema(),
            note=note,
        )
    _assert_supported_schema(schema, path="input_schema", root=True)
    return MCPPreparedSchema(
        mode="strict",
        parameters_schema=copy.deepcopy(schema),
        note=note,
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
    prepared = prepare_mcp_registration_schema(input_schema)
    if prepared.mode == "passthrough":
        if arguments is None:
            return {}
        if not isinstance(arguments, Mapping):
            raise MCPArgumentValidationError("arguments must be an object.")
        return dict(arguments)
    value = _validate_value(
        schema=input_schema,
        value=dict(arguments or {}),
        path="arguments",
    )
    if not isinstance(value, dict):
        raise MCPArgumentValidationError("arguments must validate to an object.")
    return value


def render_mcp_resource_template_uri(
    *,
    uri_template: str,
    arguments: Mapping[str, Any],
) -> str:
    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(arguments.get(key, "") or "")

    return _RESOURCE_TEMPLATE_VARIABLE_RE.sub(_replace, str(uri_template or ""))


def _passthrough_parameters_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": True}


def _classify_schema_support(
    schema: Mapping[str, Any],
    *,
    path: str,
    root: bool,
) -> tuple[str, str]:
    if root:
        try:
            schema_type, _nullable = _schema_type(schema=schema, path=path)
        except MCPUnsupportedSchemaError:
            return ("passthrough", f"{path}:missing_supported_type")
        if schema_type != "object":
            raise MCPUnsupportedSchemaError(
                "MCP only supports object-root input schemas."
            )

    for key in _UNSUPPORTED_SCHEMA_KEYS:
        if key in schema:
            return ("passthrough", f"{path}:{key}")

    if "anyOf" in schema:
        strategy = _supported_anyof_strategy(schema=schema, path=path)
        if strategy is None:
            return ("passthrough", f"{path}:anyOf")
        if strategy[0] == "nullable":
            return _classify_schema_support(
                strategy[1],
                path=f"{path}.anyOf",
                root=False,
            )
        if strategy[0] == "tagged_union":
            return ("strict", "")

    try:
        schema_type, _nullable = _schema_type(schema=schema, path=path)
    except MCPUnsupportedSchemaError:
        return ("passthrough", f"{path}:missing_supported_type")

    if schema_type == "object":
        properties = schema.get("properties", {})
        if properties is None:
            properties = {}
        if not isinstance(properties, Mapping):
            return ("passthrough", f"{path}.properties")
        additional_properties = schema.get("additionalProperties", True)
        if isinstance(additional_properties, Mapping):
            return ("passthrough", f"{path}.additionalProperties")
        for key, value in properties.items():
            if not isinstance(value, Mapping):
                return ("passthrough", f"{path}.properties.{key}")
            mode, note = _classify_schema_support(
                value,
                path=f"{path}.properties.{key}",
                root=False,
            )
            if mode == "passthrough":
                return (mode, note)
        return ("strict", "")

    if schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, Mapping):
            return ("passthrough", f"{path}.items")
        return _classify_schema_support(items, path=f"{path}.items", root=False)

    if schema_type in {"string", "integer", "number", "boolean"}:
        return ("strict", "")
    return ("passthrough", f"{path}:type:{schema_type}")


def _assert_supported_schema(
    schema: Mapping[str, Any],
    *,
    path: str,
    root: bool,
) -> None:
    for key in _UNSUPPORTED_SCHEMA_KEYS:
        if key in schema:
            raise MCPUnsupportedSchemaError(
                f"{path} uses unsupported schema feature {key!r}."
            )

    if "anyOf" in schema:
        strategy = _supported_anyof_strategy(schema=schema, path=path)
        if strategy is None:
            raise MCPUnsupportedSchemaError(
                f"{path} uses unsupported anyOf schema shape."
            )
        if strategy[0] == "nullable":
            _assert_supported_schema(strategy[1], path=f"{path}.anyOf", root=False)
            return
        if strategy[0] == "tagged_union":
            return

    schema_type, _nullable = _schema_type(schema=schema, path=path)
    if root and schema_type != "object":
        raise MCPUnsupportedSchemaError("MCP only supports object-root input schemas.")

    if schema_type == "object":
        properties = schema.get("properties", {})
        if properties is None:
            properties = {}
        if not isinstance(properties, Mapping):
            raise MCPUnsupportedSchemaError(f"{path}.properties must be an object.")
        additional_properties = schema.get("additionalProperties", True)
        if isinstance(additional_properties, Mapping):
            raise MCPUnsupportedSchemaError(
                f"{path}.additionalProperties object schemas are unsupported."
            )
        for key, value in properties.items():
            if not isinstance(value, Mapping):
                raise MCPUnsupportedSchemaError(
                    f"{path}.properties.{key} must be an object."
                )
            _assert_supported_schema(
                value,
                path=f"{path}.properties.{key}",
                root=False,
            )
        return

    if schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise MCPUnsupportedSchemaError(
                f"{path}.items must be a single homogeneous schema object."
            )
        _assert_supported_schema(items, path=f"{path}.items", root=False)
        return

    if schema_type not in {"string", "integer", "number", "boolean"}:
        raise MCPUnsupportedSchemaError(
            f"{path} uses unsupported schema type {schema_type!r}."
        )


def _validate_value(
    *,
    schema: Mapping[str, Any],
    value: Any,
    path: str,
) -> Any:
    if "anyOf" in schema:
        strategy = _supported_anyof_strategy(schema=schema, path=path)
        if strategy is None:
            raise MCPArgumentValidationError(f"{path} uses unsupported anyOf schema.")
        if strategy[0] == "nullable":
            nested_schema = strategy[1]
            if value is None:
                return None
            return _validate_value(schema=nested_schema, value=value, path=path)
        if strategy[0] == "tagged_union":
            return _validate_tagged_union_value(
                schema=schema,
                value=value,
                path=path,
                discriminator=strategy[1],
                branches=strategy[2],
            )

    schema_type, nullable = _schema_type(schema=schema, path=path)
    if value is None:
        if nullable:
            return None
        raise MCPArgumentValidationError(f"{path} cannot be null.")

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        if value not in enum_values:
            raise MCPArgumentValidationError(
                f"{path} must be one of {enum_values!r}, got {value!r}."
            )

    if schema_type == "object":
        if not isinstance(value, Mapping):
            raise MCPArgumentValidationError(f"{path} must be an object.")
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            properties = {}
        required = schema.get("required", [])
        required_names = (
            {str(item).strip() for item in required if str(item).strip()}
            if isinstance(required, list)
            else set()
        )
        additional_properties = bool(schema.get("additionalProperties", True))
        out: dict[str, Any] = {}
        for required_name in sorted(required_names):
            if required_name not in value:
                raise MCPArgumentValidationError(f"{path}.{required_name} is required.")
        for key, raw in value.items():
            key_text = str(key or "")
            property_schema = properties.get(key_text)
            if isinstance(property_schema, Mapping):
                out[key_text] = _validate_value(
                    schema=property_schema,
                    value=raw,
                    path=f"{path}.{key_text}",
                )
                continue
            if not additional_properties:
                raise MCPArgumentValidationError(
                    f"{path}.{key_text} is not allowed by the schema."
                )
            out[key_text] = raw
        for key, property_schema in properties.items():
            if key in out or not isinstance(property_schema, Mapping):
                continue
            if "default" in property_schema:
                out[str(key)] = copy.deepcopy(property_schema["default"])
        return out

    if schema_type == "array":
        if not isinstance(value, list):
            raise MCPArgumentValidationError(f"{path} must be an array.")
        item_schema = schema.get("items")
        assert isinstance(item_schema, Mapping)
        return [
            _validate_value(schema=item_schema, value=item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]

    if schema_type == "string":
        if not isinstance(value, str):
            raise MCPArgumentValidationError(f"{path} must be a string.")
        return value
    if schema_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise MCPArgumentValidationError(f"{path} must be an integer.")
        return value
    if schema_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MCPArgumentValidationError(f"{path} must be a number.")
        return value
    if schema_type == "boolean":
        if not isinstance(value, bool):
            raise MCPArgumentValidationError(f"{path} must be a boolean.")
        return value

    raise MCPArgumentValidationError(f"{path} uses unsupported type {schema_type!r}.")


def _validate_tagged_union_value(
    *,
    schema: Mapping[str, Any],
    value: Any,
    path: str,
    discriminator: str,
    branches: dict[str, Mapping[str, Any]],
) -> dict[str, Any]:
    del schema
    if not isinstance(value, Mapping):
        raise MCPArgumentValidationError(f"{path} must be an object.")
    token = value.get(discriminator)
    if not isinstance(token, str) or not token.strip():
        raise MCPArgumentValidationError(
            f"{path}.{discriminator} must be a non-empty string discriminator."
        )
    normalized = token.strip()
    if normalized not in branches:
        raise MCPArgumentValidationError(
            f"{path}.{discriminator} must be one of {sorted(branches)}."
        )
    return dict(value)


def _supported_anyof_strategy(
    *,
    schema: Mapping[str, Any],
    path: str,
) -> tuple[str, Any] | tuple[str, str, dict[str, Mapping[str, Any]]] | None:
    raw_anyof = schema.get("anyOf")
    if not isinstance(raw_anyof, list) or not raw_anyof:
        raise MCPUnsupportedSchemaError(f"{path}.anyOf must be a non-empty array.")
    branches = [item for item in raw_anyof if isinstance(item, Mapping)]
    if len(branches) != len(raw_anyof):
        raise MCPUnsupportedSchemaError(f"{path}.anyOf branches must be objects.")

    non_null_branches = []
    saw_null = False
    for branch in branches:
        branch_type = branch.get("type")
        if branch_type == "null":
            saw_null = True
            continue
        non_null_branches.append(branch)
    if saw_null and len(non_null_branches) == 1 and len(branches) == 2:
        return ("nullable", non_null_branches[0])

    tagged_union = _build_tagged_union_strategy(branches=branches, path=path)
    if tagged_union is not None:
        return tagged_union
    return None


def _build_tagged_union_strategy(
    *,
    branches: list[Mapping[str, Any]],
    path: str,
) -> tuple[str, str, dict[str, Mapping[str, Any]]] | None:
    candidate_names: set[str] | None = None
    discriminator_values_by_name: dict[str, dict[str, Mapping[str, Any]]] = {}

    for branch in branches:
        try:
            branch_type, _nullable = _schema_type(schema=branch, path=path)
        except MCPUnsupportedSchemaError:
            return None
        if branch_type != "object":
            return None
        properties = branch.get("properties")
        if not isinstance(properties, Mapping):
            return None
        branch_candidates: set[str] = set()
        for name, property_schema in properties.items():
            if not isinstance(property_schema, Mapping):
                continue
            discriminator_value = _extract_discriminator_value(property_schema)
            if (
                discriminator_value is None
                and not _looks_like_conflicting_discriminator_schema(property_schema)
            ):
                continue
            branch_candidates.add(str(name))
            if discriminator_value is None:
                continue
            discriminator_values_by_name.setdefault(str(name), {})[
                discriminator_value
            ] = branch
        if candidate_names is None:
            candidate_names = branch_candidates
        else:
            candidate_names &= branch_candidates
    if not candidate_names:
        return None

    for name in sorted(candidate_names):
        values = discriminator_values_by_name.get(name, {})
        if len(values) != len(branches):
            property_schemas = [
                dict((branch.get("properties") or {})).get(name)
                for branch in branches
                if isinstance(branch.get("properties"), Mapping)
            ]
            if any(
                _looks_like_conflicting_discriminator_schema(item)
                for item in property_schemas
            ):
                raise MCPUnsupportedSchemaError(
                    f"{path}.anyOf discriminator {name!r} has conflicting branch value types."
                )
            continue
        return ("tagged_union", name, values)
    return None


def _extract_discriminator_value(schema: Mapping[str, Any]) -> str | None:
    if "const" in schema:
        value = schema.get("const")
        if not isinstance(value, str):
            return None
        return value.strip() or None
    enum_values = schema.get("enum")
    if (
        isinstance(enum_values, list)
        and len(enum_values) == 1
        and isinstance(enum_values[0], str)
    ):
        return str(enum_values[0]).strip() or None
    return None


def _looks_like_conflicting_discriminator_schema(schema: Any) -> bool:
    if not isinstance(schema, Mapping):
        return False
    if "const" in schema:
        return not isinstance(schema.get("const"), str)
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and len(enum_values) == 1:
        return not isinstance(enum_values[0], str)
    return False


def _schema_type(*, schema: Mapping[str, Any], path: str) -> tuple[str, bool]:
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        normalized = [
            str(item).strip().lower() for item in raw_type if str(item).strip()
        ]
        nullable = "null" in normalized
        non_null = [item for item in normalized if item != "null"]
        if len(non_null) != 1:
            raise MCPUnsupportedSchemaError(
                f"{path}.type union is unsupported beyond nullable single-type schemas."
            )
        return non_null[0], nullable
    if isinstance(raw_type, str) and raw_type.strip():
        return raw_type.strip().lower(), False
    if isinstance(schema.get("properties"), Mapping):
        return "object", False
    if "enum" in schema:
        return "string", False
    raise MCPUnsupportedSchemaError(f"{path} is missing a supported type declaration.")


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
