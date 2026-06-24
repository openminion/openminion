import logging
from typing import Any, Mapping

from openminion.modules.tool.contracts import ALL_MODEL_TOOL_IDS_SET, ProviderToolSpec

_LOG = logging.getLogger("openminion.services.tool.exposure")


def _available_runtime_tool_names(registry: Any) -> set[str]:
    names: set[str] = set()
    tools = getattr(registry, "_tools", None)
    if isinstance(tools, dict):
        names.update(str(name).strip() for name in tools.keys() if str(name).strip())
    if names:
        return names

    list_fn = getattr(registry, "list", None)
    if callable(list_fn):
        try:
            listed = list_fn()
        except Exception:
            return set()
        if isinstance(listed, dict):
            names.update(
                str(name).strip() for name in listed.keys() if str(name).strip()
            )
    return names


def _canonical_specs(specs: list[ProviderToolSpec]) -> list[ProviderToolSpec]:
    canonical: list[ProviderToolSpec] = []
    seen: set[str] = set()
    for spec in specs:
        name = str(getattr(spec, "name", "") or "").strip()
        if not name or name in seen:
            continue
        if name not in ALL_MODEL_TOOL_IDS_SET:
            _LOG.debug("dropping non-canonical model exposure tool: %s", name)
            continue
        seen.add(name)
        canonical.append(spec)
    canonical.sort(key=lambda item: str(getattr(item, "name", "") or ""))
    return canonical


def get_model_exposure_specs(registry: Any) -> list[ProviderToolSpec]:
    """Return canonical model-facing tool specs without runtime-name fallback."""

    if registry is None:
        return []

    binding_manager_fn = getattr(registry, "_binding_manager", None)
    if callable(binding_manager_fn):
        try:
            manager = binding_manager_fn()
            specs = list(
                manager.model_provider_specs(_available_runtime_tool_names(registry))
            )
        except Exception:
            _LOG.warning(
                "manager-backed model tool exposure lookup failed",
                exc_info=True,
            )
            return []
        return _canonical_specs(specs)

    model_specs_fn = getattr(registry, "model_provider_specs", None)
    if callable(model_specs_fn):
        try:
            return _canonical_specs(list(model_specs_fn()))
        except Exception:
            _LOG.warning("model_provider_specs lookup failed", exc_info=True)
            return []
    return []


def get_allowed_model_tool_names(registry: Any) -> set[str]:
    return {
        str(spec.name).strip()
        for spec in get_model_exposure_specs(registry)
        if str(getattr(spec, "name", "")).strip()
    }


def get_visible_tool_specs_and_dispatch_map(
    registry: Any,
) -> tuple[list[ProviderToolSpec], dict[str, dict[str, Any]]]:
    """Return operator-visible tool specs plus dispatch metadata."""

    if registry is None:
        return [], {}

    dispatch_map: dict[str, dict[str, Any]] = {}
    dispatch_map_fn = getattr(registry, "model_runtime_dispatch_map", None)
    if callable(dispatch_map_fn):
        try:
            raw_map = dispatch_map_fn()
            if isinstance(raw_map, Mapping):
                dispatch_map = {
                    str(key).strip(): dict(value)
                    for key, value in raw_map.items()
                    if str(key).strip() and isinstance(value, Mapping)
                }
        except Exception:
            dispatch_map = {}

    specs: list[ProviderToolSpec] = []
    seen_names: set[str] = set()

    for spec in get_model_exposure_specs(registry):
        name = str(getattr(spec, "name", "") or "").strip()
        if not name or name in seen_names:
            continue
        specs.append(spec)
        seen_names.add(name)

    raw_tools = getattr(registry, "_tools", None)
    if isinstance(raw_tools, Mapping):
        provider_spec_for_name = getattr(registry, "provider_spec_for_name", None)
        for raw_name, raw_tool in raw_tools.items():
            if not bool(getattr(raw_tool, "prompt_visible_runtime_name", False)):
                continue
            name = str(raw_name or "").strip()
            if not name or name in seen_names:
                continue
            spec = None
            if callable(provider_spec_for_name):
                try:
                    spec = provider_spec_for_name(name)
                except Exception:
                    spec = None
            if spec is None:
                continue
            specs.append(spec)
            seen_names.add(name)
            runtime_binding_id = str(
                getattr(raw_tool, "runtime_binding_id", "") or ""
            ).strip()
            dispatch_map.setdefault(
                name,
                {
                    "runtime_binding_id": runtime_binding_id,
                    "runtime_tool_name": name,
                },
            )

    specs.sort(key=lambda item: str(getattr(item, "name", "") or ""))
    return specs, dispatch_map


__all__ = [
    "get_allowed_model_tool_names",
    "get_model_exposure_specs",
    "get_visible_tool_specs_and_dispatch_map",
]
