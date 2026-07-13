"""Tool-selection config models."""

from __future__ import annotations

from dataclasses import dataclass, field

from openminion.base.config.base import ConfigError

from .normalization import (
    _is_runtime_binding_id,
    _normalize_runtime_binding_selection_strategy,
    _normalize_schema_exposure,
    _normalize_tool_selection_mode,
)


@dataclass
class CapabilityBinding:
    primary: str
    fallback_tools: list[str] = field(default_factory=list)


_DEFAULT_RUNTIME_FALLBACK_ON: tuple[str, ...] = (
    "tool_unavailable",
    "transient_network_error",
    "provider_empty",
    "validation_exhausted",
    "rate limit",
    "timeout",
    "not found",
    "unavailable",
    "failed to reach",
    "connection refused",
    "connection error",
    "executable doesn't exist",
    "backend_error",
    "temporarily",
)

_DEFAULT_RUNTIME_NO_FALLBACK_ON: tuple[str, ...] = (
    "policy_denied",
    "permission",
    "forbidden",
    "auth",
    "unauthorized",
    "approval",
    "safety",
    "blocked",
    "quota_exceeded",
)


def _normalized_fallback_tokens(
    values: list[str], defaults: tuple[str, ...]
) -> list[str]:
    tokens = (str(item or "").strip().lower() for item in (values or defaults))
    return list(dict.fromkeys(item for item in tokens if item))


def _merge_capability(
    canonical_caps: dict[str, CapabilityBinding],
    *,
    mode: str,
    category: str,
    primary: str,
    fallback_tools: list[str],
) -> None:
    canonical = str(category or "").strip()
    if not canonical:
        return
    primary_token = str(primary or "").strip()
    fallback_tokens = list(
        dict.fromkeys(str(item).strip() for item in fallback_tools if str(item).strip())
    )
    existing = canonical_caps.get(canonical)
    if existing is None:
        if not primary_token and mode in ("deterministic", "typed"):
            raise ConfigError(f"Capability {canonical!r} requires a primary tool.")
        canonical_caps[canonical] = CapabilityBinding(primary_token, fallback_tokens)
        return
    if not existing.primary and primary_token:
        existing.primary = primary_token
    elif (
        primary_token
        and primary_token != existing.primary
        and primary_token not in existing.fallback_tools
    ):
        existing.fallback_tools.append(primary_token)
    existing.fallback_tools.extend(
        item
        for item in fallback_tokens
        if item != existing.primary and item not in existing.fallback_tools
    )


@dataclass
class ToolSelectionConfig:
    mode: str = "typed"
    max_tools_per_turn: int = 6
    tool_prompt_token_budget: int = 600
    enforce_required_tool_call: bool = True
    allow_runtime_direct_fallback: bool = True
    bindings: dict[str, str] = field(default_factory=dict)
    bindings_fallback: dict[str, list[str]] = field(default_factory=dict)
    capabilities: dict[str, CapabilityBinding] = field(default_factory=dict)
    runtime_bindings: dict[str, CapabilityBinding] = field(default_factory=dict)
    runtime_binding_selection_strategy: str = "ordered"
    runtime_fallback_on: list[str] = field(
        default_factory=lambda: list(_DEFAULT_RUNTIME_FALLBACK_ON)
    )
    runtime_no_fallback_on: list[str] = field(
        default_factory=lambda: list(_DEFAULT_RUNTIME_NO_FALLBACK_ON)
    )
    schema_exposure: str = "stub_first"
    validation_retry_max: int = 1

    def __post_init__(self) -> None:
        canonical_caps: dict[str, CapabilityBinding] = {}

        capability_sources = [
            (category, capability.primary, capability.fallback_tools)
            for category, capability in self.capabilities.items()
        ]
        capability_sources.extend(
            (category, primary, self.bindings_fallback.get(category, []))
            for category, primary in self.bindings.items()
        )
        for category, primary, fallback_tools in capability_sources:
            _merge_capability(
                canonical_caps,
                mode=self.mode,
                category=category,
                primary=primary,
                fallback_tools=list(fallback_tools or []),
            )

        self.capabilities = dict(sorted(canonical_caps.items()))
        self.bindings = {
            category: binding.primary
            for category, binding in sorted(self.capabilities.items())
            if str(binding.primary).strip()
        }
        self.bindings_fallback = {
            category: list(binding.fallback_tools)
            for category, binding in sorted(self.capabilities.items())
            if binding.fallback_tools
        }

        canonical_runtime_bindings: dict[str, CapabilityBinding] = {}
        for runtime_binding_id, binding in list(self.runtime_bindings.items()):
            binding_id = str(runtime_binding_id or "").strip()
            if not binding_id:
                continue
            if not _is_runtime_binding_id(binding_id):
                raise ConfigError(
                    "Invalid runtime binding id in tool_selection.runtime_bindings: "
                    f"{binding_id!r}. Expected format 'runtime.<category>.<operation>'."
                )
            fallback_tools = _normalized_fallback_tokens(
                getattr(binding, "fallback_tools", []) or [], ()
            )
            canonical_runtime_bindings[binding_id] = CapabilityBinding(
                primary=str(getattr(binding, "primary", "") or "").strip(),
                fallback_tools=list(dict.fromkeys(fallback_tools)),
            )
        self.runtime_bindings = dict(sorted(canonical_runtime_bindings.items()))

        self.mode = _normalize_tool_selection_mode(self.mode)
        self.schema_exposure = _normalize_schema_exposure(self.schema_exposure)
        normalize_strategy = _normalize_runtime_binding_selection_strategy
        self.runtime_binding_selection_strategy = normalize_strategy(
            self.runtime_binding_selection_strategy
        )
        self.runtime_fallback_on = _normalized_fallback_tokens(
            self.runtime_fallback_on, _DEFAULT_RUNTIME_FALLBACK_ON
        )
        self.runtime_no_fallback_on = _normalized_fallback_tokens(
            self.runtime_no_fallback_on, _DEFAULT_RUNTIME_NO_FALLBACK_ON
        )
