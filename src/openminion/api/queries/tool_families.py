"""Effective tool-family configuration for runtime reports."""

from typing import Any

from openminion.base.config import resolve_agent_config
from openminion.base.config.runtime.tools import coerce_tool_family_runtime_config
from openminion.tools.search.plugin import build_provider_diagnostics

_TOOL_FAMILY_NAMES = ("search", "fetch", "browser", "weather")


def _source_layer(runtime: Any, *, profile: Any, family_name: str) -> str:
    selected = resolve_agent_config(runtime.config, getattr(profile, "name", None))
    if getattr(selected.tools, family_name, None) is not None:
        return "agent_profile"
    if getattr(runtime.config.runtime.tools, family_name, None) is not None:
        return "system_runtime"
    return "code_default"


def _search_diagnostics(family_payload: dict[str, Any]) -> dict[str, Any]:
    family_config = coerce_tool_family_runtime_config(
        family_payload or None,
        family_name="search",
    )
    diagnostics: dict[str, Any] = build_provider_diagnostics(
        family_config=family_config
    )
    return diagnostics


def build_tool_family_items(
    runtime: Any,
    *,
    profile: Any,
    tool_policy: dict[str, Any],
) -> list[dict[str, Any]]:
    items = []
    for family_name in _TOOL_FAMILY_NAMES:
        family_payload = tool_policy.get(family_name, {})
        if not isinstance(family_payload, dict):
            family_payload = {}
        source_layer = _source_layer(
            runtime,
            profile=profile,
            family_name=family_name,
        )
        item = {
            "name": family_name,
            "configured": source_layer != "code_default",
            "source_layer": source_layer,
            "enabled_providers": list(
                family_payload.get("enabled_providers", []) or []
            ),
            "default_provider": str(
                family_payload.get("default_provider", "") or ""
            ).strip(),
            "provider_order": list(family_payload.get("provider_order", []) or []),
            "allow_fallback": family_payload.get("allow_fallback"),
        }
        if family_name == "search":
            item.update(_search_diagnostics(family_payload))
        items.append(item)
    return items


__all__ = ["build_tool_family_items"]
