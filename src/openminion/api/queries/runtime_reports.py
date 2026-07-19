"""API support for queries runtime reports."""

from __future__ import annotations

from typing import Any

from openminion.api.queries.mcp_reports import build_mcp_section as _build_mcp_section
from openminion.modules.brain.bootstrap.route_catalog import (
    available_routes as available_brain_routes,
    get_route_descriptor,
)
from openminion.modules.tool.exposure import get_visible_tool_specs_and_dispatch_map
from openminion.tools.mcp.exposure import (
    build_mcp_exposure_report,
    scoped_mcp_registry_view,
)

_TOOL_FAMILY_NAMES = ("search", "fetch", "browser", "weather")


def build_tool_inventory_report(
    runtime: Any,
    *,
    agent_id: str | None = None,
    profile: Any | None = None,
) -> list[dict[str, Any]]:
    registry = _tool_registry_for_agent(runtime, agent_id=agent_id, profile=profile)
    provider_specs, dispatch_map = get_visible_tool_specs_and_dispatch_map(registry)
    runtime_tools = _tool_runtime_lookup(registry)
    inventory: list[dict[str, Any]] = []
    for spec in provider_specs:
        mapping = (
            dict(dispatch_map.get(spec.name, {}) or {})
            if isinstance(dispatch_map, dict)
            else {}
        )
        runtime_tool_name = str(mapping.get("runtime_tool_name", "") or "").strip()
        runtime_tool = runtime_tools.get(runtime_tool_name) or runtime_tools.get(
            spec.name
        )
        item = {
            "name": spec.name,
            "description": spec.description,
            "parameters": dict(spec.parameters),
            "enabled": True,
            "policy_allowed": True,
            "runtime_binding_id": str(
                mapping.get("runtime_binding_id", "") or ""
            ).strip(),
            "runtime_tool_name": runtime_tool_name,
            "source": _tool_source_label(
                model_tool_name=str(spec.name or "").strip(),
                runtime_tool_name=runtime_tool_name,
                runtime_tool=runtime_tool,
            ),
        }
        plugin_origin = _tool_plugin_origin(runtime_tool)
        if plugin_origin:
            item["plugin_origin"] = plugin_origin
        inventory.append(item)
    return sorted(inventory, key=lambda item: item["name"])


def build_tool_schema_report(runtime: Any, *, tool_name: str) -> dict[str, Any] | None:
    normalized = str(tool_name or "").strip()
    if not normalized:
        return None
    for item in build_tool_inventory_report(runtime):
        if item["name"] == normalized:
            return dict(item)
    return None


def _build_provider_items(runtime: Any, diagnostics: dict[str, Any]) -> dict[str, Any]:
    provider_section = diagnostics.get("provider", {})
    effective_providers = {
        str(item).strip()
        for item in provider_section.get("effective_enabled", [])
        if str(item).strip()
    }
    items = []
    for provider_name in _provider_candidate_ids(runtime.config, diagnostics):
        items.append(
            {
                "name": provider_name,
                "enabled": provider_name in effective_providers,
                "selected": provider_name
                == str(provider_section.get("selected", "") or "").strip(),
                "blocked_reason": ""
                if provider_name in effective_providers
                else "blocked by the effective provider allowlist",
            }
        )
    return {
        "selected": str(provider_section.get("selected", "") or "").strip(),
        "source_layer": str(provider_section.get("source", "") or "").strip(),
        "effective_enabled": sorted(effective_providers),
        "provider_order": list(provider_section.get("provider_order", []) or []),
        "system_default_provider": str(
            provider_section.get("system_default_provider", "") or ""
        ).strip(),
        "agent_default_provider": str(
            provider_section.get("agent_default_provider", "") or ""
        ).strip(),
        "invocation_requested_provider": str(
            provider_section.get("invocation_requested_provider", "") or ""
        ).strip(),
        "items": items,
    }


def _build_mode_items(
    diagnostics: dict[str, Any],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    mode_section = diagnostics.get("modes", {})
    blocked_mode_reasons = {
        str(name).strip(): str(reason or "").strip()
        for name, reason in dict(mode_section.get("blocked_reasons", {}) or {}).items()
        if str(name).strip()
    }
    effective_modes = {
        str(name).strip()
        for name, payload in dict(mode_section.get("effective", {}) or {}).items()
        if str(name).strip() and bool(dict(payload or {}).get("enabled", True))
    }
    items = []
    for mode_name in sorted(set(available_brain_routes()) | set(blocked_mode_reasons)):
        mode_spec = get_route_descriptor(mode_name)
        registration_source = (
            dict(mode_spec.registration_source or {}) if mode_spec is not None else {}
        )
        thinking_policy = getattr(mode_spec, "thinking_policy", None)
        items.append(
            {
                "name": mode_name,
                "enabled": mode_name in effective_modes,
                "blocked_reason": blocked_mode_reasons.get(mode_name, ""),
                "registration_source": registration_source,
                "thinking_policy": (
                    {
                        "default_reasoning_profile": (
                            thinking_policy.default_reasoning_profile
                        ),
                        "allowed_reasoning_profiles": list(
                            thinking_policy.allowed_reasoning_profiles or ()
                        )
                        if thinking_policy is not None
                        else [],
                        "allow_request_override": bool(
                            getattr(thinking_policy, "allow_request_override", True)
                        ),
                    }
                    if thinking_policy is not None
                    else None
                ),
            }
        )
    return blocked_mode_reasons, items


def _build_plugin_items(runtime: Any, diagnostics: dict[str, Any]) -> dict[str, Any]:
    plugin_section = diagnostics.get("plugins", {})
    effective_plugins = {
        str(item).strip()
        for item in plugin_section.get("effective_enabled", [])
        if str(item).strip()
    }
    blocked_plugins = {
        str(item).strip()
        for item in plugin_section.get("blocked", [])
        if str(item).strip()
    }
    plugin_candidates = sorted(
        {
            *(
                str(item).strip()
                for item in getattr(runtime.config, "enabled_plugins", [])
                if str(item).strip()
            ),
            *effective_plugins,
            *blocked_plugins,
            *(
                str(item).strip()
                for item in runtime.plugins.manifest_ids()
                if str(item).strip()
            ),
        }
    )
    items = []
    for plugin_name in plugin_candidates:
        items.append(
            {
                "name": plugin_name,
                "enabled": plugin_name in effective_plugins,
                "blocked_reason": (
                    "blocked by runtime plugin policy"
                    if plugin_name in blocked_plugins
                    else ""
                ),
            }
        )
    return {
        "source_layer": str(plugin_section.get("source", "") or "").strip(),
        "effective_enabled": sorted(effective_plugins),
        "blocked": sorted(blocked_plugins),
        "items": items,
    }


def _build_tool_family_items(tool_policy: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for family_name in _TOOL_FAMILY_NAMES:
        family_payload = tool_policy.get(family_name, {})
        if not isinstance(family_payload, dict):
            family_payload = {}
        items.append(
            {
                "name": family_name,
                "configured": bool(family_payload),
                "enabled_providers": list(
                    family_payload.get("enabled_providers", []) or []
                ),
                "default_provider": str(
                    family_payload.get("default_provider", "") or ""
                ).strip(),
                "provider_order": list(family_payload.get("provider_order", []) or []),
                "allow_fallback": family_payload.get("allow_fallback"),
            }
        )
    return items


def _build_tools_section(
    runtime: Any,
    diagnostics: dict[str, Any],
    *,
    agent_id: str | None,
    profile: Any,
) -> dict[str, Any]:
    tool_policy = dict(diagnostics.get("tools", {}) or {})
    tool_inventory = build_tool_inventory_report(
        runtime,
        agent_id=agent_id,
        profile=profile,
    )
    runtime_bound_count = sum(
        1
        for item in tool_inventory
        if str(item.get("runtime_binding_id", "") or "").strip()
    )
    plugin_origin_count = sum(1 for item in tool_inventory if item.get("plugin_origin"))
    return {
        "policy": tool_policy,
        "families": _build_tool_family_items(tool_policy),
        "inventory": tool_inventory,
        "counts": {
            "total": len(tool_inventory),
            "runtime_bound": runtime_bound_count,
            "plugin_origin": plugin_origin_count,
        },
        "mcp_exposure": build_mcp_exposure_report(
            registry=getattr(runtime, "tools", None),
            server_configs=list(
                getattr(runtime.config.runtime, "mcp_servers", []) or []
            ),
            exposure=getattr(profile, "mcp_exposure", None),
        ),
    }


def build_capability_report(
    runtime: Any,
    *,
    agent_id: str | None = None,
    overrides=None,
) -> dict[str, Any]:
    diagnostics = runtime.capability_runtime_diagnostics(
        agent_id=agent_id,
        overrides=overrides,
    )
    profile = runtime.resolve_agent_profile(agent_id, overrides=overrides)
    thinking_section = dict(diagnostics.get("thinking", {}) or {})
    providers = _build_provider_items(runtime, diagnostics)
    blocked_mode_reasons, mode_items = _build_mode_items(diagnostics)
    plugins = _build_plugin_items(runtime, diagnostics)
    tools = _build_tools_section(
        runtime,
        diagnostics,
        agent_id=agent_id,
        profile=profile,
    )
    mcp = _build_mcp_section(runtime)
    return {
        "agent": profile.name,
        "providers": providers,
        "modes": {
            "blocked_reasons": blocked_mode_reasons,
            "items": mode_items,
        },
        "thinking": thinking_section,
        "plugins": plugins,
        "tools": tools,
        "mcp": mcp,
    }


def build_runtime_posture_report(
    runtime: Any,
    *,
    agent_id: str | None = None,
    overrides=None,
    canonical_turn_path: tuple[str, ...],
    canonical_turn_path_ref: str,
    execution_boundary_policy_ref: str,
    capability_layering_ref: str,
) -> dict[str, Any]:
    profile = runtime.resolve_agent_profile(agent_id, overrides=overrides)
    agent_service = runtime.resolve_agent_service(profile.name, overrides=overrides)
    runtime_info = runtime.get_agent_runtime_info(profile.name, overrides=overrides)
    capability_report = build_capability_report(
        runtime,
        agent_id=profile.name,
        overrides=overrides,
    )
    tool_budget = getattr(runtime.security_policy, "tool_budget_policy", None)
    bridge_diag_fn = getattr(agent_service, "bridge_diagnostics", None)
    bridge_diagnostics_payload: dict[str, Any] | None = None
    if callable(bridge_diag_fn):
        try:
            bridge_diagnostics_payload = bridge_diag_fn()
        except Exception:  # noqa: BLE001
            bridge_diagnostics_payload = None
    posture: dict[str, Any] = {
        "agent": profile.name,
        "runtime_mode": str(runtime_info.get("runtime_mode", "unknown") or "unknown"),
        "brain_bridge_active": bool(runtime_info.get("brain_bridge_active", False)),
        "fallback_reason": str(runtime_info.get("fallback_reason", "") or "").strip(),
        "canonical_turn_path": list(canonical_turn_path),
        "canonical_turn_path_ref": canonical_turn_path_ref,
        "execution_boundary_policy": {
            "owner": execution_boundary_policy_ref,
            "adapter": "execution-boundary",
            "default_required_scopes": sorted(
                str(scope).strip()
                for scope in (
                    getattr(
                        runtime.security_policy,
                        "default_tool_required_scopes",
                        frozenset(),
                    )
                    or frozenset()
                )
                if str(scope).strip()
            ),
            "max_calls_per_run": int(getattr(tool_budget, "max_calls_per_run", 0) or 0),
            "max_calls_per_tool": int(
                getattr(tool_budget, "max_calls_per_tool", 0) or 0
            ),
            "max_budget_cost_per_run": int(
                getattr(tool_budget, "max_budget_cost_per_run", 0) or 0
            ),
        },
        "capability_layering": {
            "provider_selected": str(
                capability_report["providers"].get("selected", "") or ""
            ).strip(),
            "provider_source_layer": str(
                capability_report["providers"].get("source_layer", "") or ""
            ).strip(),
            "modes_blocked": sorted(
                str(name).strip()
                for name in capability_report["modes"].get("blocked_reasons", {})
                if str(name).strip()
            ),
            "plugins_enabled": list(
                capability_report["plugins"].get("effective_enabled", []) or []
            ),
            "tools_configured_families": [
                item["name"]
                for item in capability_report["tools"].get("families", [])
                if item.get("configured")
            ],
            "ref": capability_layering_ref,
        },
    }
    if bridge_diagnostics_payload is not None:
        posture["bridge_diagnostics"] = bridge_diagnostics_payload
    return posture


def _tool_runtime_lookup(registry: Any) -> dict[str, Any]:
    list_fn = getattr(registry, "list", None)
    if not callable(list_fn):
        return {}
    try:
        listed = list_fn()
    except Exception:
        return {}
    if not isinstance(listed, dict):
        return {}
    return {
        str(name).strip(): tool for name, tool in listed.items() if str(name).strip()
    }


def _tool_registry_for_agent(
    runtime: Any,
    *,
    agent_id: str | None,
    profile: Any | None = None,
) -> Any:
    registry = getattr(runtime, "tools", None)
    if registry is None:
        return None
    selected_profile = profile
    if selected_profile is None and callable(
        getattr(runtime, "resolve_agent_profile", None)
    ):
        try:
            selected_profile = runtime.resolve_agent_profile(agent_id)
        except Exception:
            selected_profile = None
    return scoped_mcp_registry_view(
        registry,
        getattr(selected_profile, "mcp_exposure", None),
    )


def _tool_plugin_origin(runtime_tool: object | None) -> str:
    if runtime_tool is None:
        return ""
    for attr_name in ("plugin_origin", "plugin_id"):
        value = str(getattr(runtime_tool, attr_name, "") or "").strip()
        if value:
            return value
    return ""


def _tool_source_label(
    *,
    model_tool_name: str,
    runtime_tool_name: str,
    runtime_tool: object | None,
) -> str:
    plugin_origin = _tool_plugin_origin(runtime_tool)
    if plugin_origin:
        return "plugin"
    runtime_name = str(runtime_tool_name or "").strip()
    if runtime_name.startswith("mcp."):
        return "mcp"
    module_name = ""
    if runtime_tool is not None:
        module_name = str(
            getattr(type(runtime_tool), "__module__", "")
            or getattr(runtime_tool, "__module__", "")
            or ""
        ).strip()
    if module_name.startswith("openminion.tools."):
        parts = module_name.split(".")
        if len(parts) >= 4 and parts[3]:
            return parts[3]
    return "core" if model_tool_name == runtime_name or not runtime_name else "routed"


def _provider_candidate_ids(config: Any, diagnostics: dict[str, Any]) -> list[str]:
    provider_section = diagnostics.get("provider", {})
    candidates = {"echo"}
    providers_cfg = getattr(config, "providers", None)
    if providers_cfg is not None:
        candidates.update(
            str(name).strip()
            for name in vars(providers_cfg).keys()
            if str(name).strip()
        )
    for key in (
        "selected",
        "system_default_provider",
        "agent_default_provider",
        "invocation_requested_provider",
    ):
        value = str(provider_section.get(key, "") or "").strip()
        if value:
            candidates.add(value)
    candidates.update(
        str(item).strip()
        for item in provider_section.get("effective_enabled", [])
        if str(item).strip()
    )
    candidates.update(
        str(item).strip()
        for item in provider_section.get("provider_order", [])
        if str(item).strip()
    )
    return sorted(candidates)
