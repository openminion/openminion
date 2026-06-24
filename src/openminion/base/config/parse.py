"""Small config coercion helpers."""

from __future__ import annotations

from typing import Any

from openminion.base.constants import BASE_BOOL_FALSE_VALUES, BASE_BOOL_TRUE_VALUES
from openminion.base.config.base import ConfigError


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in BASE_BOOL_TRUE_VALUES:
            return True
        if normalized in BASE_BOOL_FALSE_VALUES:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _as_int_list(value: Any) -> list[int]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = [part.strip() for part in value.split(",") if part.strip()]
    else:
        items = []
    return [parsed for item in items if (parsed := _positive_int(item)) is not None]


def _as_string_dict(value: Any, *, lower_keys: bool) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip()
        val = str(raw_value or "").strip()
        if not key or not val:
            continue
        if lower_keys:
            key = key.lower()
        normalized[key] = val
    return dict(sorted(normalized.items()))


def _normalize_channel_authenticity_mode(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"off", "warn", "require"}:
        return value
    return "warn"


def _normalize_self_improvement_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"review-first", "review_first"}:
        return "review_first"
    if normalized in {"auto", "automatic"}:
        return "automatic"
    return "automatic"


def _normalize_process_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"single-process", "single_process", "inproc", "in-process"}:
        return "single-process"
    if normalized in {"daemon", "service"}:
        return "daemon"
    return "daemon"


def _normalize_complex_request_plan_policy(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"off", "disabled", "none"}:
        return "off"
    if normalized in {"conservative", "strict"}:
        return "conservative"
    if normalized in {"balanced", "default"}:
        return "balanced"
    if normalized in {"aggressive", "high"}:
        return "aggressive"
    return "balanced"


def _normalize_memory_capsule_strategy(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"frozen", "frozen_session", "session", "snapshot"}:
        return "frozen_session"
    if normalized in {"dynamic", "dynamic_turn", "turn", "per_turn"}:
        return "dynamic_turn"
    if normalized in {"refresh_on_write", "write"}:
        return "refresh_on_write"
    if normalized in {"off", "disabled", "none"}:
        return "off"
    return "dynamic_turn"


def _normalize_memory_provider(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "memory_v2"}:
        return "memory_v2"
    if normalized == "memory_v2_hello_world":
        return "memory_v2_hello_world"
    raise ConfigError(
        "Invalid runtime.memory_provider. "
        "Supported values: memory_v2, memory_v2_hello_world."
    )


def _normalize_identity_budget_truncate_strategy(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"sentence", "sentences"}:
        return "sentences"
    if normalized in {"bullet", "bullets"}:
        return "bullets"
    return "sentences"


def _normalize_brain_integration_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "ctxctl_authoritative"}:
        return "contextctl_authoritative"
    if normalized == "contextctl_authoritative":
        return normalized
    if normalized == "legacy_compat":
        raise ConfigError(
            "gateway.brain_integration_mode=legacy_compat is no longer supported. "
            "Use contextctl_authoritative."
        )
    raise ConfigError(
        "Invalid gateway.brain_integration_mode. "
        "Supported value: contextctl_authoritative."
    )


def _as_str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return str(value)


def _as_non_empty_str(value: Any, *, default: str) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            return normalized
    return default


def _as_obj(value: Any, default: dict[str, Any]) -> dict[str, Any]:
    return value if isinstance(value, dict) else dict(default)


__all__ = [
    "_as_int",
    "_as_float",
    "_as_optional_float",
    "_as_bool",
    "_as_int_list",
    "_as_str_or_none",
    "_as_non_empty_str",
    "_as_obj",
    "_normalize_channel_authenticity_mode",
    "_normalize_self_improvement_mode",
    "_normalize_process_mode",
    "_normalize_complex_request_plan_policy",
    "_normalize_memory_capsule_strategy",
    "_normalize_memory_provider",
    "_normalize_identity_budget_truncate_strategy",
    "_normalize_brain_integration_mode",
]
