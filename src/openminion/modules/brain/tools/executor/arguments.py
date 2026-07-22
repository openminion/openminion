from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...constants import BRAIN_COMMAND_KIND_TOOL
from ...schemas import Command
from ..parser import normalize_tool_name_for_brain

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...runner import BrainRunner


_JSON_SCHEMA_TOP_LEVEL_KEYS = frozenset(
    {
        "$defs",
        "$schema",
        "additionalProperties",
        "allOf",
        "anyOf",
        "description",
        "oneOf",
        "properties",
        "required",
        "title",
        "type",
    }
)


def _spec_like_payload(entry: Any) -> dict[str, Any] | None:
    if isinstance(entry, dict):
        return dict(entry)
    name = str(getattr(entry, "name", "") or "").strip()
    parameters = getattr(entry, "parameters", None)
    if not name:
        return None
    return {
        "name": name,
        "parameters": dict(parameters) if isinstance(parameters, dict) else parameters,
    }


def _parameter_keys_from_spec_payload(spec_payload: dict[str, Any] | None) -> set[str]:
    if not isinstance(spec_payload, dict):
        return set()
    raw_parameters = spec_payload.get("parameters")
    if not isinstance(raw_parameters, dict) or not raw_parameters:
        return set()
    properties = raw_parameters.get("properties")
    if isinstance(properties, dict) and properties:
        return {str(key).strip() for key in properties.keys() if str(key or "").strip()}
    if any(key in raw_parameters for key in _JSON_SCHEMA_TOP_LEVEL_KEYS):
        return set()
    return {str(key).strip() for key in raw_parameters.keys() if str(key or "").strip()}


def resolve_tool_spec_payload(
    runner: "BrainRunner",
    *,
    tool_name: str,
) -> dict[str, Any] | None:
    normalized_name = normalize_tool_name_for_brain(tool_name)
    candidate_names = [
        item
        for item in [str(tool_name or "").strip(), str(normalized_name or "").strip()]
        if item
    ]
    tool_api = getattr(runner, "tool_api", None)
    list_tools = getattr(tool_api, "list_tools", None)
    if callable(list_tools):
        try:
            for entry in list(list_tools() or []):
                payload = _spec_like_payload(entry)
                if payload is None:
                    continue
                if str(payload.get("name", "") or "").strip() in candidate_names:
                    return payload
        except (AttributeError, KeyError, TypeError, ValueError):
            pass
    registry = getattr(tool_api, "registry", None)
    getter = getattr(registry, "get", None)
    if callable(getter):
        for candidate in candidate_names:
            try:
                payload = _spec_like_payload(getter(candidate))
            except (AttributeError, KeyError, TypeError, ValueError):
                payload = None
            if payload is not None:
                return payload
    tools_dict = getattr(registry, "_tools", None)
    if isinstance(tools_dict, dict):
        for candidate in candidate_names:
            payload = _spec_like_payload(tools_dict.get(candidate))
            if payload is not None:
                return payload
    return None


def sanitize_tool_command_args(
    runner: "BrainRunner",
    *,
    command: Command,
) -> tuple[dict[str, Any], list[str]]:
    if command.kind != BRAIN_COMMAND_KIND_TOOL:
        return {}, []
    existing_args = getattr(command, "args", {})
    if not isinstance(existing_args, dict):
        return {}, []
    known_keys = _parameter_keys_from_spec_payload(
        resolve_tool_spec_payload(
            runner,
            tool_name=str(getattr(command, "tool_name", "") or ""),
        )
    )
    if not known_keys:
        return dict(existing_args), []
    sanitized = {key: value for key, value in existing_args.items() if key in known_keys}
    removed = [str(key) for key in existing_args.keys() if key not in known_keys]
    if removed:
        command.args = dict(sanitized)
    return dict(command.args), removed


__all__ = [
    "_JSON_SCHEMA_TOP_LEVEL_KEYS",
    "_parameter_keys_from_spec_payload",
    "_spec_like_payload",
    "resolve_tool_spec_payload",
    "sanitize_tool_command_args",
]
