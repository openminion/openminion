from typing import Any

from openminion.base.config.runtime.reasoning import resolve_runtime_reasoning_config

from .constants import (
    THINKING_DEGRADE_UNKNOWN_PROFILE,
    THINKING_DEGRADE_MODE_CLAMP,
    THINKING_DEGRADE_REQUEST_OVERRIDE_BLOCKED,
    THINKING_SOURCE_AGENT_RUNTIME,
    THINKING_SOURCE_CAPABILITY_DEFINITION,
    THINKING_SOURCE_MODE_POLICY,
    THINKING_SOURCE_SYSTEM_RUNTIME,
)
from .mapping import (
    normalize_optional_reasoning_profile,
    provider_effort_for_profile,
    resolve_provider_effort_support,
)
from .schemas import (
    ModeThinkingPolicy,
    ThinkingRequest,
    ThinkingResolved,
    ThinkingResolutionInput,
    ThinkingRuntimeDiagnostics,
)

_PROFILE_RANK = {
    "off": 0,
    "minimal": 1,
    "detailed": 2,
}


def _profile_rank(profile: str) -> int:
    return _PROFILE_RANK.get(str(profile or "").strip(), 1)


def _normalized_allowed_profiles(
    policy: ModeThinkingPolicy | None,
) -> tuple[str, ...]:
    if policy is None or policy.allowed_reasoning_profiles is None:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for item in policy.allowed_reasoning_profiles:
        candidate = normalize_optional_reasoning_profile(item)
        if candidate is None or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return tuple(normalized)


def _clamp_profile_to_allowed(
    *,
    reasoning_profile: str,
    allowed_profiles: tuple[str, ...],
) -> str:
    if not allowed_profiles:
        return reasoning_profile
    allowed_sorted = sorted(allowed_profiles, key=_profile_rank)
    current_rank = _profile_rank(reasoning_profile)
    eligible = [item for item in allowed_sorted if _profile_rank(item) <= current_rank]
    if eligible:
        return eligible[-1]
    return allowed_sorted[0]


def resolve_thinking(
    *,
    request: ThinkingRequest,
    layers: ThinkingResolutionInput,
) -> ThinkingResolved:
    return resolve_mode_aware_thinking(
        request=request,
        layers=layers,
        mode_policy=None,
        mode_name=None,
    )


def resolve_mode_aware_thinking(
    *,
    request: ThinkingRequest,
    layers: ThinkingResolutionInput,
    mode_policy: ModeThinkingPolicy | None,
    mode_name: str | None = None,
) -> ThinkingResolved:
    raw_request_profile = (
        request.requested_profile
        if request.requested_profile is not None
        else layers.request_profile
    )
    config_resolution = resolve_runtime_reasoning_config(
        code_default_profile=layers.code_default_profile,
        system_profile=layers.system_profile,
        agent_profile=layers.agent_profile,
        invocation_requested_profile=raw_request_profile,
        provider_name=request.provider,
        model_name=request.model,
    )
    requested_profile = config_resolution.requested_profile
    system_profile = config_resolution.system_profile
    agent_profile = config_resolution.agent_profile
    code_default_profile = (
        normalize_optional_reasoning_profile(layers.code_default_profile) or "minimal"
    )
    mode_default_profile = normalize_optional_reasoning_profile(
        getattr(mode_policy, "default_reasoning_profile", None)
    )
    allowed_profiles = _normalized_allowed_profiles(mode_policy)
    allow_request_override = (
        True if mode_policy is None else bool(mode_policy.allow_request_override)
    )

    degraded_reasons: list[str] = []
    if config_resolution.unknown_request_profile:
        degraded_reasons.append(THINKING_DEGRADE_UNKNOWN_PROFILE)

    reasoning_profile = config_resolution.reasoning_profile
    source_layer = config_resolution.source_layer
    if requested_profile is not None and not allow_request_override:
        reasoning_profile = agent_profile or system_profile or code_default_profile
        source_layer = (
            THINKING_SOURCE_AGENT_RUNTIME
            if agent_profile is not None
            else THINKING_SOURCE_SYSTEM_RUNTIME
            if system_profile is not None
            else THINKING_SOURCE_CAPABILITY_DEFINITION
        )
        degraded_reasons.append(THINKING_DEGRADE_REQUEST_OVERRIDE_BLOCKED)

    if (
        mode_default_profile is not None
        and source_layer == THINKING_SOURCE_CAPABILITY_DEFINITION
    ):
        reasoning_profile = mode_default_profile
        source_layer = THINKING_SOURCE_MODE_POLICY

    if allowed_profiles:
        clamped_profile = _clamp_profile_to_allowed(
            reasoning_profile=reasoning_profile,
            allowed_profiles=allowed_profiles,
        )
        if clamped_profile != reasoning_profile:
            reasoning_profile = clamped_profile
            source_layer = THINKING_SOURCE_MODE_POLICY
            degraded_reasons.append(THINKING_DEGRADE_MODE_CLAMP)

    provider_effort = provider_effort_for_profile(reasoning_profile)
    supported, effective_effort, support_degrade_reason = (
        resolve_provider_effort_support(
            provider_name=request.provider,
            model_name=request.model,
            provider_effort=provider_effort,
        )
    )
    if support_degrade_reason:
        degraded_reasons.append(support_degrade_reason)

    return ThinkingResolved(
        requested_profile=requested_profile,
        reasoning_profile=reasoning_profile,
        provider_effort=effective_effort,
        source_layer=source_layer,
        supported=supported,
        degraded_reason=degraded_reasons[0] if degraded_reasons else None,
        degraded_reasons=tuple(degraded_reasons),
        provider_name=str(request.provider or "").strip(),
        model_name=str(request.model or "").strip(),
        system_profile=system_profile,
        agent_profile=agent_profile,
        request_override_profile=requested_profile,
        mode_name=str(mode_name or "").strip() or None,
        mode_default_profile=mode_default_profile,
        mode_allowed_profiles=allowed_profiles,
        mode_request_override_allowed=allow_request_override,
    )


def build_runtime_thinking_diagnostics(
    *,
    code_default_profile: str,
    system_profile: str | None,
    agent_profile: str | None,
    invocation_requested_profile: str | None,
    provider_name: str | None,
    model_name: str | None,
    purpose: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ThinkingRuntimeDiagnostics:
    request = ThinkingRequest(
        purpose=purpose,
        requested_profile=invocation_requested_profile,
        provider=provider_name,
        model=model_name,
        metadata=dict(metadata or {}),
    )
    layers = ThinkingResolutionInput(
        code_default_profile=code_default_profile,
        system_profile=system_profile,
        agent_profile=agent_profile,
        request_profile=invocation_requested_profile,
    )
    effective = resolve_thinking(request=request, layers=layers)
    return ThinkingRuntimeDiagnostics(
        code_default_profile=(
            normalize_optional_reasoning_profile(code_default_profile) or "minimal"
        ),
        system_profile=normalize_optional_reasoning_profile(system_profile),
        agent_profile=normalize_optional_reasoning_profile(agent_profile),
        invocation_requested_profile=normalize_optional_reasoning_profile(
            invocation_requested_profile
        ),
        effective=effective,
    )
