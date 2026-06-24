from collections.abc import Mapping
from typing import Any

from openminion.base.config.runtime.tools import (
    ToolFamilyRuntimeConfig,
    ToolRuntimeConfig,
    coerce_tool_runtime_config,
    tool_runtime_config_to_dict,
)


def build_runtime_tool_routing_metadata(runtime_tools: object) -> dict[str, Any]:
    payload = tool_runtime_config_to_dict(coerce_tool_runtime_config(runtime_tools))
    if not payload:
        return {}
    return {"runtime_tools": payload}


def _context_metadata(context: Any | None) -> Mapping[str, Any]:
    if context is None:
        return {}
    direct = getattr(context, "metadata", None)
    if isinstance(direct, Mapping):
        return direct

    raw = getattr(context, "raw", None)
    if isinstance(raw, Mapping):
        metadata = raw.get("context_metadata")
        if isinstance(metadata, Mapping):
            return metadata

    policy = getattr(context, "policy", None)
    raw = getattr(policy, "raw", None)
    if not isinstance(raw, Mapping):
        return {}
    metadata = raw.get("context_metadata")
    if isinstance(metadata, Mapping):
        return metadata
    return {}


def resolve_runtime_tool_config(context: Any | None) -> ToolRuntimeConfig:
    metadata = _context_metadata(context)
    raw = metadata.get("runtime_tools")
    if not isinstance(raw, Mapping):
        return ToolRuntimeConfig()
    return coerce_tool_runtime_config(raw)


def resolve_runtime_tool_family_config(
    context: Any | None,
    *,
    family_name: str,
) -> ToolFamilyRuntimeConfig | None:
    normalized_family = str(family_name or "").strip().lower()
    if not normalized_family:
        return None
    config = resolve_runtime_tool_config(context)
    return getattr(config, normalized_family, None)


def resolve_runtime_provider_chain(
    *,
    available: list[str] | tuple[str, ...],
    family_config: ToolFamilyRuntimeConfig | None,
    hinted_order: list[str] | tuple[str, ...] = (),
) -> list[str]:
    normalized_available: list[str] = []
    seen_available: set[str] = set()
    for item in available:
        token = str(item or "").strip().lower()
        if not token or token in seen_available:
            continue
        seen_available.add(token)
        normalized_available.append(token)
    available_set = set(normalized_available)

    allowed = list(normalized_available)
    if family_config is not None and family_config.enabled_providers:
        enabled = {
            str(item or "").strip().lower()
            for item in family_config.enabled_providers
            if str(item or "").strip()
        }
        allowed = [item for item in normalized_available if item in enabled]

    ordered: list[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        token = str(candidate or "").strip().lower()
        if not token or token in seen or token not in available_set:
            return
        if token not in allowed:
            return
        seen.add(token)
        ordered.append(token)

    if family_config is not None:
        for candidate in family_config.provider_order:
            _add(candidate)
        if family_config.default_provider:
            _add(family_config.default_provider)

    for candidate in hinted_order:
        _add(candidate)

    for candidate in allowed:
        _add(candidate)

    if family_config is not None and family_config.allow_fallback is False and ordered:
        return [ordered[0]]
    return ordered


__all__ = [
    "build_runtime_tool_routing_metadata",
    "resolve_runtime_provider_chain",
    "resolve_runtime_tool_config",
    "resolve_runtime_tool_family_config",
]
