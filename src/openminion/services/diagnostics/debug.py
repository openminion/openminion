from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openminion.base.debug import (
    DebugProvider,
    DebugRegistry,
    DebugStatus,
    ModuleDebugPayload,
    WiringSource,
    create_path_debug_payload,
    get_debug_registry,
    set_debug_registry,
)
from openminion.base.config import OpenMinionConfig


@dataclass
class ToolSelectionDebugPayload:
    mode: str = "hybrid"
    shortlist_size: int = 0
    token_estimate: int = 0
    selected_tool: Optional[str] = None
    category: Optional[str] = None
    binding_source: Optional[str] = None
    fallback_used: bool = False
    reason_codes: List[str] = field(default_factory=list)
    validation_retry_count: int = 0
    schema_expanded: bool = False

    # Capability-related debug fields from CBGF-10
    capability_category: Optional[str] = None
    capability_primary: Optional[str] = None
    capability_fallback_chain: Optional[List[str]] = field(default_factory=list)
    capability_attempted_tools: Optional[List[str]] = field(default_factory=list)
    capability_fallback_trigger_reason: Optional[str] = None
    capability_final_tool: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "mode": self.mode,
            "shortlist_size": self.shortlist_size,
            "token_estimate": self.token_estimate,
            "selected_tool": self.selected_tool,
            "category": self.category or "none",
            "binding_source": self.binding_source or "none",
            "fallback_used": self.fallback_used,
            "reason_codes": self.reason_codes,
            "validation_retry_count": self.validation_retry_count,
            "schema_expanded": self.schema_expanded,
        }
        if self.capability_category:
            result["capability_category"] = self.capability_category
        if self.capability_primary:
            result["capability_primary"] = self.capability_primary
        if self.capability_fallback_chain:
            result["capability_fallback_chain"] = list(self.capability_fallback_chain)
        if self.capability_attempted_tools:
            result["capability_attempted_tools"] = list(self.capability_attempted_tools)
        if self.capability_fallback_trigger_reason:
            result["capability_fallback_trigger_reason"] = (
                self.capability_fallback_trigger_reason
            )
        if self.capability_final_tool:
            result["capability_final_tool"] = self.capability_final_tool
        return result


def create_tool_selection_debug_payload(
    mode: str = "hybrid",
    shortlist_size: int = 0,
    token_estimate: int = 0,
    selected_tool: Optional[str] = None,
    category: Optional[str] = None,
    binding_source: Optional[str] = None,
    fallback_used: bool = False,
    reason_codes: Optional[List[str]] = None,
    validation_retry_count: int = 0,
    schema_expanded: bool = False,
    **capability_kwargs: Any,
) -> ToolSelectionDebugPayload:
    payload = ToolSelectionDebugPayload(
        mode=mode,
        shortlist_size=shortlist_size,
        token_estimate=token_estimate,
        selected_tool=selected_tool,
        category=category,
        binding_source=binding_source,
        fallback_used=fallback_used,
        reason_codes=reason_codes or [],
        validation_retry_count=validation_retry_count,
        schema_expanded=schema_expanded,
    )
    for key, value in capability_kwargs.items():
        if hasattr(payload, key):
            setattr(payload, key, value)
    return payload


def _load_debug_provider_module(module_path: str) -> None:
    try:
        __import__(module_path)
    except ImportError:
        return


def load_debug_providers() -> None:
    get_debug_registry()
    # Controlplane telegram debug provider
    _load_debug_provider_module(
        "openminion.modules.controlplane.channels.telegram.debug_provider"
    )
    # Execution-boundary policy posture
    _load_debug_provider_module("openminion.modules.policy.diagnostics.debug_provider")


def is_debug_surface_enabled(
    config: OpenMinionConfig | Dict[str, Any], *, surface: str
) -> bool:
    runtime = None
    if isinstance(config, dict):
        runtime = (
            config.get("runtime", {})
            if isinstance(config.get("runtime", {}), dict)
            else {}
        )
    else:
        runtime = getattr(config, "runtime", None)

    def _get_runtime_flag(key: str, default: bool = True) -> bool:
        if isinstance(runtime, dict):
            return bool(runtime.get(key, default))
        return (
            bool(getattr(runtime, key, default))
            if runtime is not None
            else bool(default)
        )

    if not _get_runtime_flag("debug_enabled", True):
        return False
    normalized = str(surface or "").strip().lower()
    if normalized == "api":
        return _get_runtime_flag("debug_api_enabled", True)
    if normalized == "cli":
        return _get_runtime_flag("debug_cli_enabled", True)
    if normalized == "chat":
        return _get_runtime_flag("debug_chat_enabled", True)
    if normalized in {"module", "module_probes", "probes"}:
        return _get_runtime_flag("debug_module_probes_enabled", True)
    return _get_runtime_flag("debug_enabled", True)


__all__ = [
    "DebugStatus",
    "WiringSource",
    "ModuleDebugPayload",
    "create_path_debug_payload",
    "DebugProvider",
    "DebugRegistry",
    "get_debug_registry",
    "set_debug_registry",
    "ToolSelectionDebugPayload",
    "create_tool_selection_debug_payload",
    "load_debug_providers",
    "is_debug_surface_enabled",
]
