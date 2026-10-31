from openminion.base.config.runtime.reasoning import (
    normalize_optional_reasoning_profile,
    reasoning_profile_was_unknown,
)

from .constants import (
    THINKING_DEGRADE_PROVIDER_UNSUPPORTED,
    THINKING_REASONING_PROFILE_OFF,
    THINKING_ROUTE_BRIDGE,
    THINKING_ROUTE_OLLAMA,
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
    route: str | None = None,
) -> tuple[bool, str | None, str | None]:
    normalized_provider = (provider_name or "").strip().lower()
    del model_name
    if provider_effort is None or not normalized_provider:
        return True, provider_effort, None
    route_key = (route or "").strip().lower()
    if route_key == THINKING_ROUTE_OLLAMA:
        # The ollama adapter maps the resolved effort to an explicit `think`
        # flag on its request payload, so the effort is transmitted.
        return True, provider_effort, None
    if route_key == THINKING_ROUTE_BRIDGE:
        # The llmctl bridge resolves the effort and forwards it through the
        # shared provider metadata contract. Keep the provider-name allowlist so
        # an unknown provider name still degrades, and never claim the echo
        # adapter transmits effort: it only reflects the requested value back in
        # its reply text instead of sending it to a provider.
        if normalized_provider in THINKING_SUPPORTED_PROVIDER_NAMES:
            if normalized_provider == "echo":
                return False, None, THINKING_DEGRADE_PROVIDER_UNSUPPORTED
            return True, provider_effort, None
        return False, None, THINKING_DEGRADE_PROVIDER_UNSUPPORTED
    # Direct OpenAI/Anthropic/Groq/Cerebras/OpenRouter adapters build request
    # payloads that never carry the resolved effort, and an unestablished route
    # cannot prove any adapter transmitted it.
    return False, None, THINKING_DEGRADE_PROVIDER_UNSUPPORTED
