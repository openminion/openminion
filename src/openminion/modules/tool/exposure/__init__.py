from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from openminion.modules.tool.contracts import ALL_MODEL_TOOL_IDS_SET, ProviderToolSpec

from .contracts import (
    ToolCatalogCard,
    ToolExposureDecision,
    ToolExposureProfile,
    ToolExposureSession,
    ToolRiskAnnotations,
)
from .defaults import default_exposure_profiles, requires_explicit_exposure_profile
from .service import ToolExposureService, exposure_scope

_LOG = logging.getLogger(__name__)


def _available_runtime_tool_names(registry: Any) -> set[str]:
    tools = getattr(registry, "_tools", None)
    if isinstance(tools, dict):
        return {str(name).strip() for name in tools if str(name).strip()}
    list_fn = getattr(registry, "list", None)
    if not callable(list_fn):
        return set()
    try:
        listed = list_fn()
    except Exception:
        return set()
    return (
        {str(name).strip() for name in listed if str(name).strip()}
        if isinstance(listed, dict)
        else set()
    )


def _canonical_specs(specs: list[ProviderToolSpec]) -> list[ProviderToolSpec]:
    canonical: dict[str, ProviderToolSpec] = {}
    for spec in specs:
        name = str(getattr(spec, "name", "") or "").strip()
        if not name or name in canonical:
            continue
        if name not in ALL_MODEL_TOOL_IDS_SET:
            _LOG.debug("dropping non-canonical model exposure tool: %s", name)
            continue
        canonical[name] = spec
    return [canonical[name] for name in sorted(canonical)]


def get_model_exposure_specs(
    registry: Any,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> list[ProviderToolSpec]:
    """Return canonical model-facing specs filtered by explicit exposure state."""

    if registry is None:
        return []
    binding_manager_fn = getattr(registry, "_binding_manager", None)
    if callable(binding_manager_fn):
        try:
            specs = list(
                binding_manager_fn().model_provider_specs(
                    _available_runtime_tool_names(registry)
                )
            )
        except Exception:
            _LOG.warning(
                "manager-backed model tool exposure lookup failed", exc_info=True
            )
            return []
    else:
        model_specs_fn = getattr(registry, "model_provider_specs", None)
        if not callable(model_specs_fn):
            return []
        try:
            specs = list(model_specs_fn())
        except Exception:
            _LOG.warning("model_provider_specs lookup failed", exc_info=True)
            return []
    canonical = _canonical_specs(specs)
    service = getattr(registry, "exposure_service", None)
    if not isinstance(service, ToolExposureService):
        return canonical
    return service.filter_specs(canonical, **exposure_scope(metadata))


def get_allowed_model_tool_names(
    registry: Any,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> set[str]:
    return {spec.name for spec in get_model_exposure_specs(registry, metadata=metadata)}


def get_visible_tool_specs_and_dispatch_map(
    registry: Any,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[list[ProviderToolSpec], dict[str, dict[str, Any]]]:
    """Return visible tool specs plus canonical dispatch metadata."""

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
    specs = get_model_exposure_specs(registry, metadata=metadata)
    visible = {spec.name for spec in specs}

    raw_tools = getattr(registry, "_tools", None)
    if isinstance(raw_tools, Mapping):
        provider_spec_for_name = getattr(registry, "provider_spec_for_name", None)
        service = getattr(registry, "exposure_service", None)
        scope = exposure_scope(metadata)
        for raw_name, raw_tool in raw_tools.items():
            if not bool(getattr(raw_tool, "prompt_visible_runtime_name", False)):
                continue
            name = str(raw_name or "").strip()
            if not name or name in visible:
                continue
            if isinstance(service, ToolExposureService):
                if service.decide(name, **scope).state != "visible":
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
            visible.add(name)
            dispatch_map.setdefault(
                name,
                {
                    "runtime_binding_id": str(
                        getattr(raw_tool, "runtime_binding_id", "") or ""
                    ).strip(),
                    "runtime_tool_name": name,
                },
            )

    specs.sort(key=lambda item: str(getattr(item, "name", "") or ""))
    dispatch_map = {
        name: value for name, value in dispatch_map.items() if name in visible
    }
    return specs, dispatch_map


def render_catalog_cards(
    cards: tuple[ToolCatalogCard, ...],
    *,
    available_tool_names: set[str],
) -> str:
    """Render active profile cards that own at least one registered tool."""

    lines: list[str] = []
    for card in cards:
        tool_names = tuple(
            name for name in card.tool_names if name in available_tool_names
        )
        if not tool_names:
            continue
        lines.append(f"- {card.title} [{card.profile_id}]: {card.summary}")
        lines.append(f"  tier: {card.tier}; tools: {', '.join(tool_names)}")
        if card.target_ids:
            lines.append(f"  targets: {', '.join(card.target_ids)}")
        if card.expires_at is not None:
            lines.append(f"  expires_at: {card.expires_at:.0f}")
        if card.evidence_expectations:
            lines.append(f"  evidence: {'; '.join(card.evidence_expectations)}")
        if card.stop_rules:
            lines.append(f"  stop: {'; '.join(card.stop_rules)}")
        if card.guidance_names:
            lines.append(f"  guidance: {', '.join(card.guidance_names)}")
    if not lines:
        return ""
    return "<active_tool_profiles>\n" + "\n".join(lines) + "\n</active_tool_profiles>"


def apply_model_exposure(request: Any, registry: Any) -> None:
    """Apply model-visible tool filtering and active catalog cards."""

    metadata = getattr(request, "metadata", {}) or {}
    service = getattr(registry, "exposure_service", None)
    if request.tools:
        if isinstance(service, ToolExposureService):
            request.tools = service.filter_specs(
                request.tools,
                **exposure_scope(metadata),
            )
    else:
        request.tools = get_model_exposure_specs(registry, metadata=metadata)
    if not isinstance(service, ToolExposureService):
        return
    card_block = render_catalog_cards(
        service.cards(**exposure_scope(metadata)),
        available_tool_names={spec.name for spec in request.tools},
    )
    if card_block and card_block not in request.system_prompt:
        request.system_prompt = f"{request.system_prompt.rstrip()}\n\n{card_block}"


__all__ = [
    "ToolCatalogCard",
    "ToolExposureDecision",
    "ToolExposureProfile",
    "ToolExposureService",
    "ToolExposureSession",
    "ToolRiskAnnotations",
    "apply_model_exposure",
    "default_exposure_profiles",
    "exposure_scope",
    "get_allowed_model_tool_names",
    "get_model_exposure_specs",
    "get_visible_tool_specs_and_dispatch_map",
    "render_catalog_cards",
    "requires_explicit_exposure_profile",
]
