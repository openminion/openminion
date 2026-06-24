"""Tool-selection payload parsing."""

from __future__ import annotations

from openminion.base.config.base import ConfigError
from openminion.base.config.parse import _as_bool, _as_int

from .models import (
    CapabilityBinding,
    ToolSelectionConfig,
    _DEFAULT_RUNTIME_FALLBACK_ON,
    _DEFAULT_RUNTIME_NO_FALLBACK_ON,
)
from .normalization import (
    _normalize_runtime_binding_selection_strategy,
    _normalize_schema_exposure,
    _normalize_tool_selection_mode,
)


def _parse_category_map(raw_value: object) -> dict[str, str]:
    bindings: dict[str, str] = {}
    if not isinstance(raw_value, dict):
        return bindings
    for key, value in raw_value.items():
        normalized_key = str(key or "").strip()
        normalized_value = str(value or "").strip()
        if normalized_key and normalized_value:
            bindings[normalized_key] = normalized_value
    return bindings


def _parse_string_list(raw_value: object) -> list[str]:
    if isinstance(raw_value, str):
        return [part.strip() for part in raw_value.split(",") if part.strip()]
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    return []


def _parse_fallback_bindings(raw_value: object) -> dict[str, list[str]]:
    bindings_fallback: dict[str, list[str]] = {}
    if not isinstance(raw_value, dict):
        return bindings_fallback
    for key, value in raw_value.items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        fallback_tools = _parse_string_list(value)
        if fallback_tools:
            bindings_fallback[normalized_key] = fallback_tools
    return bindings_fallback


def _parse_capability_binding_map(
    raw_value: object,
    *,
    field_path: str,
    require_primary: bool,
) -> dict[str, CapabilityBinding]:
    bindings: dict[str, CapabilityBinding] = {}
    if not isinstance(raw_value, dict):
        return bindings
    for raw_key, raw_binding in raw_value.items():
        binding_id = str(raw_key or "").strip()
        if not binding_id:
            continue
        if not isinstance(raw_binding, dict):
            raise ConfigError(
                f"{field_path} entries must be objects with 'primary' and optional "
                "'fallback_tools'."
            )
        primary = str(raw_binding.get("primary", "")).strip()
        fallback_tools = sorted(
            _parse_string_list(raw_binding.get("fallback_tools", []))
        )
        if require_primary and not primary:
            raise ConfigError(
                "Missing required primary tool for capability category "
                f"'{binding_id}'. Every deterministic category must have exactly one "
                "primary tool configured."
            )
        bindings[binding_id] = CapabilityBinding(
            primary=primary,
            fallback_tools=fallback_tools,
        )
    return bindings


def _parse_runtime_policy_tokens(
    raw_value: object,
    *,
    default_tokens: tuple[str, ...],
) -> list[str]:
    tokens = _parse_string_list(raw_value)
    if not tokens:
        return list(default_tokens)
    return [token.lower() for token in tokens]


def _parse_tool_selection_config(value: object) -> ToolSelectionConfig:
    if not isinstance(value, dict):
        return ToolSelectionConfig()

    mode = _normalize_tool_selection_mode(str(value.get("mode") or ""))
    max_tools = max(1, min(20, _as_int(value.get("max_tools_per_turn"), 6)))
    token_budget = max(
        100, min(2000, _as_int(value.get("tool_prompt_token_budget"), 600))
    )
    enforce_required_tool_call = _as_bool(
        value.get("enforce_required_tool_call"),
        True,
    )
    allow_runtime_direct_fallback = _as_bool(
        value.get("allow_runtime_direct_fallback"),
        False,
    )
    validation_retry = max(0, min(3, _as_int(value.get("validation_retry_max"), 1)))
    schema_exposure = _normalize_schema_exposure(
        str(value.get("schema_exposure") or "")
    )
    runtime_binding_selection_strategy = _normalize_runtime_binding_selection_strategy(
        str(value.get("runtime_binding_selection_strategy") or "")
    )

    bindings = _parse_category_map(value.get("bindings"))
    bindings_fallback = _parse_fallback_bindings(value.get("bindings_fallback"))
    capabilities = _parse_capability_binding_map(
        value.get("capabilities"),
        field_path="tool_selection.capabilities",
        require_primary=True,
    )
    runtime_bindings = _parse_capability_binding_map(
        value.get("runtime_bindings"),
        field_path="tool_selection.runtime_bindings",
        require_primary=False,
    )
    runtime_fallback_on = _parse_runtime_policy_tokens(
        value.get("runtime_fallback_on"),
        default_tokens=_DEFAULT_RUNTIME_FALLBACK_ON,
    )
    runtime_no_fallback_on = _parse_runtime_policy_tokens(
        value.get("runtime_no_fallback_on"),
        default_tokens=_DEFAULT_RUNTIME_NO_FALLBACK_ON,
    )

    return ToolSelectionConfig(
        mode=mode,
        max_tools_per_turn=max_tools,
        tool_prompt_token_budget=token_budget,
        enforce_required_tool_call=enforce_required_tool_call,
        allow_runtime_direct_fallback=allow_runtime_direct_fallback,
        bindings=dict(sorted(bindings.items())),
        bindings_fallback={k: sorted(v) for k, v in sorted(bindings_fallback.items())},
        capabilities={k: v for k, v in sorted(capabilities.items())},
        runtime_bindings={k: v for k, v in sorted(runtime_bindings.items())},
        runtime_binding_selection_strategy=runtime_binding_selection_strategy,
        runtime_fallback_on=runtime_fallback_on,
        runtime_no_fallback_on=runtime_no_fallback_on,
        schema_exposure=schema_exposure,
        validation_retry_max=validation_retry,
    )
