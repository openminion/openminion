import logging
from typing import TYPE_CHECKING, Any
from collections.abc import Mapping

from openminion.modules.tool.contracts import (
    ProviderToolSpec,
    normalize_raw_model_tool_name,
)
from .catalog import ToolSpec
from openminion.tools.config import resolve_tool_env

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry


_ALLOW_MODEL_EXPOSURE_PROVIDER_FALLBACK_ENV = (
    "OPENMINION_ALLOW_MODEL_EXPOSURE_PROVIDER_FALLBACK"
)


def provider_specs(registry: "ToolRegistry") -> list[ProviderToolSpec]:
    """Build the canonical ProviderToolSpec list from registered tools."""
    result: list[ProviderToolSpec] = []
    for tool in registry._tools.values():
        if isinstance(tool, ToolSpec):
            description = ""
            parameters: dict[str, Any] = {}
            explicit_parameters = getattr(tool, "parameters_schema", None)
            if isinstance(explicit_parameters, Mapping) and explicit_parameters:
                parameters = dict(explicit_parameters)
            args_model = getattr(tool, "args_model", None)
            if not parameters and hasattr(args_model, "model_json_schema"):
                schema = args_model.model_json_schema()
                if isinstance(schema, Mapping):
                    parameters = dict(schema)
                    description = str(parameters.get("description", "") or "").strip()
            elif not parameters and args_model in {dict, None}:
                parameters = {"type": "object", "additionalProperties": True}
            result.append(
                ProviderToolSpec(
                    name=tool.name,
                    description=description
                    or str(getattr(tool, "name", "")).strip()
                    or "tool",
                    parameters=parameters,
                )
            )
        else:
            result.append(tool.provider_spec())
    return result


def model_provider_specs(registry: "ToolRegistry") -> list[ProviderToolSpec]:
    """Return canonical model-facing ProviderToolSpec list with safe fallback."""
    manager = registry._binding_manager()
    specs = manager.model_provider_specs(set(registry._tools.keys()))
    if not specs and registry._tools:
        env_owner = resolve_tool_env()
        allow_fallback = str(
            env_owner.get(_ALLOW_MODEL_EXPOSURE_PROVIDER_FALLBACK_ENV, "")
        ).strip().lower() in {"1", "true", "yes", "on"}
        if allow_fallback:
            logging.getLogger(__name__).warning(
                "Canonical model tool exposure is empty; using legacy provider_specs fallback. "
                "Set %s=0 to fail closed.",
                _ALLOW_MODEL_EXPOSURE_PROVIDER_FALLBACK_ENV,
            )
            return registry.provider_specs()
        sample = ", ".join(sorted(registry._tools.keys())[:10])
        raise RuntimeError(  # allow-bare-raise: fail-closed invariant — empty canonical exposure surface
            "Canonical model tool exposure is empty; refusing silent provider_specs fallback. "
            f"runtime_tool_count={len(registry._tools)} sample=[{sample}] "
            f"(override with {_ALLOW_MODEL_EXPOSURE_PROVIDER_FALLBACK_ENV}=1 if needed)"
        )
    specs.sort(key=lambda item: item.name)
    return specs


def model_to_runtime_binding_map(registry: "ToolRegistry") -> dict[str, str]:
    manager = registry._binding_manager()
    return manager.model_to_runtime_binding_map()


def model_to_runtime_tool_map(registry: "ToolRegistry") -> dict[str, str]:
    manager = registry._binding_manager()
    return manager.model_to_runtime_tool_map(set(registry._tools.keys()))


def model_runtime_dispatch_map(
    registry: "ToolRegistry",
) -> dict[str, dict[str, Any]]:
    manager = registry._binding_manager()
    return manager.model_runtime_dispatch_map(set(registry._tools.keys()))


def registration_debug_snapshot(registry: "ToolRegistry") -> dict[str, Any]:
    """Build a debug snapshot of registration state for telemetry/diagnostics."""
    runtime_tools = sorted(registry._tools.keys())
    manager = registry._binding_manager()
    binding_map = manager.model_to_runtime_binding_map()
    runtime_tool_map = manager.model_to_runtime_tool_map(set(runtime_tools))
    dispatch_map = manager.model_runtime_dispatch_map(set(runtime_tools))

    tracked_runtime_tools = {
        tool for tool in runtime_tool_map.values() if str(tool).strip()
    }
    untracked_runtime_tools = [
        tool_name
        for tool_name in runtime_tools
        if tool_name not in tracked_runtime_tools
    ]

    runtime_bindings = []
    for model_tool_id, runtime_binding_id in sorted(binding_map.items()):
        dispatch = dict(dispatch_map.get(model_tool_id, {}) or {})
        runtime_tool_name = str(dispatch.get("runtime_tool_name", "") or "").strip()
        available_candidates = []
        if runtime_tool_name:
            available_candidates.append(runtime_tool_name)
        runtime_bindings.append(
            {
                "runtime_binding_id": runtime_binding_id,
                "model_tool_id": model_tool_id,
                "runtime_tool_name": runtime_tool_name,
                "available_candidates": available_candidates,
                "resolvable": bool(runtime_tool_name),
            }
        )

    unresolved_runtime_binding_ids = [
        runtime_binding_id
        for model_tool_id, runtime_binding_id in sorted(binding_map.items())
        if model_tool_id not in runtime_tool_map
    ]

    return {
        "runtime_tool_count": len(runtime_tools),
        "runtime_tools": runtime_tools,
        "model_provider_spec_count": len(registry.model_provider_specs()),
        "manager": {
            "runtime_binding_count": len(binding_map),
            "runtime_bindings": runtime_bindings,
            "unresolved_runtime_binding_ids": unresolved_runtime_binding_ids,
        },
        "untracked_runtime_tools": untracked_runtime_tools,
    }


def provider_spec_for_name(
    registry: "ToolRegistry", name: str
) -> ProviderToolSpec | None:
    """Resolve a ProviderToolSpec by tool name (runtime or model-facing)."""
    token = str(name or "").strip()
    if not token:
        return None

    direct = provider_spec_for_runtime_name(registry, token)
    if direct is not None:
        return direct

    normalized_model_name = normalize_raw_model_tool_name(token)
    if normalized_model_name:
        for item in registry.model_provider_specs():
            if item.name == normalized_model_name:
                return item
    return None


def provider_spec_for_runtime_name(
    registry: "ToolRegistry", tool_name: str
) -> ProviderToolSpec | None:
    """Resolve a ProviderToolSpec for a registered runtime tool name."""
    tool = registry._tools.get(str(tool_name or "").strip())
    if tool is None:
        return None
    if isinstance(tool, ToolSpec):
        description = ""
        parameters: dict[str, Any] = {}
        explicit_parameters = getattr(tool, "parameters_schema", None)
        if isinstance(explicit_parameters, Mapping) and explicit_parameters:
            parameters = dict(explicit_parameters)
        args_model = getattr(tool, "args_model", None)
        if not parameters and hasattr(args_model, "model_json_schema"):
            schema = args_model.model_json_schema()
            if isinstance(schema, Mapping):
                parameters = dict(schema)
                description = str(parameters.get("description", "") or "").strip()
        elif not parameters and args_model in {dict, None}:
            parameters = {"type": "object", "additionalProperties": True}
        return ProviderToolSpec(
            name=tool.name,
            description=description or str(getattr(tool, "name", "")).strip() or "tool",
            parameters=parameters,
        )
    return tool.provider_spec()
