import re
from typing import Any, Mapping, Optional

from openminion.modules.llm.providers.base import ProviderToolSpec
from openminion.modules.tool.dispatch import _get_registry_manager
from .prompts import (
    build_required_tool_retry_prompt as _render_required_tool_retry_prompt,
)


def _is_generic_open_object_schema(schema: Mapping[str, Any]) -> bool:
    if str(schema.get("type", "")).strip() != "object":
        return False
    properties = schema.get("properties")
    if isinstance(properties, dict) and properties:
        return False
    return schema.get("additionalProperties") is True


def _parameters_for_spec(spec: Optional[ProviderToolSpec]) -> dict[str, Any]:
    if spec is None:
        return {}
    parameters = getattr(spec, "parameters", {}) or {}
    if isinstance(parameters, dict):
        normalized_parameters = dict(parameters)
        if normalized_parameters and not _is_generic_open_object_schema(
            normalized_parameters
        ):
            return normalized_parameters

    manager = _get_registry_manager()
    if manager is not None and callable(getattr(manager, "schema_for", None)):
        try:
            schema = manager.schema_for(str(getattr(spec, "name", "") or ""))
        except Exception:
            schema = {}
        if isinstance(schema, dict):
            normalized = dict(schema)
            if normalized and not _is_generic_open_object_schema(normalized):
                return normalized
    if isinstance(parameters, dict):
        return dict(parameters)
    return {}


def required_fields_from_spec(spec: Optional[ProviderToolSpec]) -> list[str]:
    parameters = _parameters_for_spec(spec)
    if not parameters:
        return []
    required_fields: list[str] = []
    required = parameters.get("required", [])
    if isinstance(required, list):
        for item in required:
            token = str(item).strip()
            if token:
                required_fields.append(token)
    any_of = parameters.get("anyOf", [])
    if isinstance(any_of, list):
        for entry in any_of:
            if not isinstance(entry, dict):
                continue
            for item in entry.get("required", []) or []:
                token = str(item).strip()
                if token:
                    required_fields.append(token)
    seen: set[str] = set()
    ordered: list[str] = []
    for item in required_fields:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def normalize_required_tool_arguments(
    *,
    tool_name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    del tool_name
    return dict(arguments or {})


def sanitize_arguments_for_spec(
    *,
    arguments: Mapping[str, Any],
    spec: Optional[ProviderToolSpec],
) -> dict[str, Any]:
    normalized = dict(arguments or {})
    parameters = _parameters_for_spec(spec)
    if not parameters:
        return normalized
    properties = parameters.get("properties", {})
    if not isinstance(properties, dict) or not properties:
        return normalized
    allowed = {str(key).strip() for key in properties.keys() if str(key).strip()}
    if not allowed:
        return normalized
    return {key: value for key, value in normalized.items() if key in allowed}


def build_required_tool_retry_prompt(
    *,
    user_message: str,
    tool_name: str,
    spec: ProviderToolSpec,
) -> str:
    return _render_required_tool_retry_prompt(
        user_message=user_message,
        tool_name=tool_name,
        required_fields=required_fields_from_spec(spec),
    )


def is_argument_error(result: Any) -> bool:
    if getattr(result, "ok", False):
        return False
    error = str(getattr(result, "error", "")).lower()
    patterns = (
        "missing",
        "required",
        "invalid tool arguments",
        "invalid arguments",
        "validation",
        "schema",
    )
    return any(pattern in error for pattern in patterns)


def extract_missing_fields(results: list[Any]) -> str:
    missing_fields: list[str] = []
    for result in results:
        error = str(getattr(result, "error", "")).strip()
        if not error:
            continue
        match = re.search(
            r"missing\s+([A-Za-z0-9_.,\s-]+?)\s+(?:argument|field|fields?)",
            error,
            flags=re.IGNORECASE,
        )
        if match:
            raw = match.group(1)
            for token in re.split(r"[,/\s]+", raw):
                normalized = token.strip().lower()
                if normalized and normalized not in {"and", "or"}:
                    missing_fields.append(normalized)
        elif "missing" in error.lower():
            missing_fields.append("unknown")
    if not missing_fields:
        return ""
    return ",".join(sorted(set(missing_fields)))


def missing_required_for_call(
    tool_call: Any,
    *,
    spec_lookup,
) -> list[str]:
    tool_name = str(getattr(tool_call, "name", "")).strip()
    if not tool_name:
        return []
    spec = spec_lookup(tool_name)
    if spec is None:
        return []
    required = required_fields_from_spec(spec)
    if not required:
        return []
    arguments = getattr(tool_call, "arguments", {}) or {}
    if not isinstance(arguments, dict):
        arguments = {}
    missing: list[str] = []
    for key in required:
        if key not in arguments:
            missing.append(key)
            continue
        value = arguments.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(key)
    return missing


def collect_missing_required(
    tool_calls: list[Any],
    *,
    spec_lookup,
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for call in tool_calls:
        tool_name = str(getattr(call, "name", "")).strip() or "unknown_tool"
        missing = missing_required_for_call(call, spec_lookup=spec_lookup)
        if missing:
            result[tool_name] = missing
    return result
