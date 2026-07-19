from openminion.base.config.runtime.reasoning import (
    normalize_optional_reasoning_profile,
    reasoning_profile_was_unknown,
)

from .constants import (
    THINKING_DEGRADE_PROVIDER_UNSUPPORTED,
    THINKING_REASONING_PROFILE_OFF,
    THINKING_SUPPORTED_PROVIDER_NAMES,
)

__all__ = [
    "normalize_optional_reasoning_profile",
    "provider_effort_for_profile",
    "reasoning_profile_was_unknown",
    "resolve_provider_effort_support",
]


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
