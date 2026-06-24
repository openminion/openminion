from typing import Any

from .constants import (
    THINKING_DEGRADE_PROVIDER_UNSUPPORTED,
    THINKING_REASONING_PROFILE_DETAILED,
    THINKING_REASONING_PROFILE_MINIMAL,
    THINKING_REASONING_PROFILE_OFF,
    THINKING_REASONING_PROFILES,
    THINKING_SUPPORTED_PROVIDER_NAMES,
)

_OFF_ALIASES = frozenset({"0", "disabled", "false", "none", "no", "off"})
_MINIMAL_ALIASES = frozenset(
    {
        "",
        "default",
        "light",
        "low",
        "min",
        "minimal",
        "normal",
    }
)
_DETAILED_ALIASES = frozenset(
    {
        "deep",
        "detailed",
        "full",
        "hard",
        "harder",
        "high",
        "max",
        "verbose",
    }
)
_KNOWN_ALIAS_TOKENS = (
    _OFF_ALIASES
    | _MINIMAL_ALIASES
    | _DETAILED_ALIASES
    | frozenset(THINKING_REASONING_PROFILES)
)


def normalize_optional_reasoning_profile(raw_value: Any) -> str | None:
    token = str(raw_value or "").strip().lower()
    if not token:
        return None
    if token in _OFF_ALIASES:
        return THINKING_REASONING_PROFILE_OFF
    if token in _DETAILED_ALIASES:
        return THINKING_REASONING_PROFILE_DETAILED
    return THINKING_REASONING_PROFILE_MINIMAL


def reasoning_profile_was_unknown(raw_value: Any) -> bool:
    token = str(raw_value or "").strip().lower()
    return bool(token) and token not in _KNOWN_ALIAS_TOKENS


def provider_effort_for_profile(profile: str) -> str | None:
    normalized = normalize_optional_reasoning_profile(profile)
    if normalized in {None, THINKING_REASONING_PROFILE_OFF}:
        return None
    return normalized


def resolve_provider_effort_support(
    *,
    provider_name: str | None,
    model_name: str | None,
    provider_effort: str | None,
) -> tuple[bool, str | None, str | None]:
    normalized_provider = str(provider_name or "").strip().lower()
    del model_name
    if provider_effort is None:
        return True, None, None
    if not normalized_provider:
        return True, provider_effort, None
    if normalized_provider in THINKING_SUPPORTED_PROVIDER_NAMES:
        return True, provider_effort, None
    return False, None, THINKING_DEGRADE_PROVIDER_UNSUPPORTED
