from openminion.base.config.runtime.reasoning import (
    normalize_optional_reasoning_profile,
    reasoning_profile_was_unknown,
)

from .constants import (
    THINKING_DEGRADE_PROVIDER_UNSUPPORTED,
    THINKING_METADATA_DEGRADED_REASON,
    THINKING_METADATA_PROVIDER_EFFORT,
    THINKING_METADATA_SUPPORTED,
    THINKING_REASONING_PROFILE_OFF,
    THINKING_ROUTE_BRIDGE,
    THINKING_ROUTE_DIRECT,
    THINKING_ROUTE_OLLAMA,
    THINKING_SUPPORTED_PROVIDER_NAMES,
)

__all__ = [
    "normalize_optional_reasoning_profile",
    "provider_effort_for_profile",
    "reasoning_profile_was_unknown",
    "reconcile_route_support_metadata",
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
        # The bridge forwards effort through the shared metadata contract; the
        # echo adapter only reflects it, and unknown providers still degrade.
        if normalized_provider in THINKING_SUPPORTED_PROVIDER_NAMES:
            if normalized_provider == "echo":
                return False, None, THINKING_DEGRADE_PROVIDER_UNSUPPORTED
            return True, provider_effort, None
        return False, None, THINKING_DEGRADE_PROVIDER_UNSUPPORTED
    if route_key == THINKING_ROUTE_DIRECT:
        # Direct OpenAI/Anthropic/Groq/Cerebras/OpenRouter adapters build
        # request payloads that never carry the resolved effort.
        return False, None, THINKING_DEGRADE_PROVIDER_UNSUPPORTED
    # No explicit route (the base-config/capability path resolves effort without
    # knowing the execution route): keep the original provider-allowlist
    # capability behavior so reasoning parity holds for supported providers and
    # only unknown providers degrade.
    if normalized_provider in THINKING_SUPPORTED_PROVIDER_NAMES:
        return True, provider_effort, None
    return False, None, THINKING_DEGRADE_PROVIDER_UNSUPPORTED


def reconcile_route_support_metadata(
    *,
    reasoning_profile: str,
    provider_name: str | None,
    model_name: str | None,
    route: str | None,
    metadata: dict[str, str],
) -> dict[str, str]:
    """Recompute provider-effort CAPABILITY for the actual route in resolved metadata.

    The brain resolves the reasoning profile (SELECTION) without knowing the
    execution route, so the `thinking_supported` / `thinking_provider_effort`
    values it emitted are provisional. Re-derive only the CAPABILITY (support)
    for the actual route and leave the selected profile and any non-provider
    degrade reasons untouched.
    """
    provider_effort = provider_effort_for_profile(reasoning_profile)
    supported, effective_effort, support_degrade_reason = (
        resolve_provider_effort_support(
            provider_name=provider_name,
            model_name=model_name,
            provider_effort=provider_effort,
            route=route,
        )
    )
    metadata[THINKING_METADATA_SUPPORTED] = str(bool(supported)).lower()
    if effective_effort is not None:
        metadata[THINKING_METADATA_PROVIDER_EFFORT] = effective_effort
        metadata["thinking"] = effective_effort
    else:
        metadata.pop(THINKING_METADATA_PROVIDER_EFFORT, None)
        metadata.pop("thinking", None)
    _reconcile_provider_support_degraded(metadata, support_degrade_reason)
    return metadata


def _reconcile_provider_support_degraded(
    metadata: dict[str, str],
    support_degrade_reason: str | None,
) -> None:
    raw = metadata.get("thinking_degraded_reasons") or metadata.get(
        THINKING_METADATA_DEGRADED_REASON
    )
    reasons = _split_degrade_reasons(raw)
    reasons = [
        reason for reason in reasons if reason != THINKING_DEGRADE_PROVIDER_UNSUPPORTED
    ]
    if support_degrade_reason and support_degrade_reason not in reasons:
        reasons.append(support_degrade_reason)
    if reasons:
        metadata["thinking_degraded_reasons"] = ",".join(reasons)
        metadata[THINKING_METADATA_DEGRADED_REASON] = reasons[0]
    else:
        metadata.pop("thinking_degraded_reasons", None)
        metadata.pop(THINKING_METADATA_DEGRADED_REASON, None)


def _split_degrade_reasons(raw: str | None) -> list[str]:
    if not raw:
        return []
    text = str(raw).strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1]
        parts = [part.strip().strip("\"'") for part in inner.split(",")]
        return [part for part in parts if part]
    return [part.strip() for part in text.split(",") if part.strip()]
