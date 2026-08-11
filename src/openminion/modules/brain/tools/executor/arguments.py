from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...constants import BRAIN_COMMAND_KIND_TOOL
from ...schemas import Command
from ...tool_catalog import RunnerToolCatalog
from ...tool_catalog.runtime import _schema_payload as _spec_like_payload

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
    return RunnerToolCatalog(runner).get_tool_schema(tool_name)


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
    sanitized = {
        key: value for key, value in existing_args.items() if key in known_keys
    }
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
