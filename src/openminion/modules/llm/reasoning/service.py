from typing import Any

from openminion.base.version import OPENMINION_REASONING_VERSION

from .constants import (
    THINKING_HINT_DEGRADED_REASON,
    THINKING_HINT_EFFECTIVE_PROFILE,
    THINKING_HINT_PROVIDER_EFFORT,
    THINKING_HINT_REQUESTED_PROFILE,
    THINKING_HINT_SOURCE_LAYER,
    THINKING_HINT_SUPPORTED,
    THINKING_METADATA_DEGRADED_REASON,
    THINKING_METADATA_MODEL,
    THINKING_METADATA_PROVIDER,
    THINKING_METADATA_PROVIDER_EFFORT,
    THINKING_METADATA_REASONING_PROFILE,
    THINKING_METADATA_REQUESTED_PROFILE,
    THINKING_METADATA_SOURCE_LAYER,
    THINKING_METADATA_SUPPORTED,
)
from .interfaces import THINKING_INTERFACE_VERSION
from .mapping import normalize_optional_reasoning_profile
from .resolver import (
    resolve_mode_aware_thinking,
    resolve_thinking,
)
from .schemas import (
    ModeThinkingPolicy,
    ThinkingRequest,
    ThinkingResolved,
    ThinkingResolutionInput,
)


class ThinkingCtl:
    """Thinking/reasoning service controller."""

    contract_version = THINKING_INTERFACE_VERSION

    def is_enabled(self) -> bool:
        return True

    def get_version(self) -> str:
        return OPENMINION_REASONING_VERSION

    def normalize_profile(self, raw_value: Any) -> str | None:
        return normalize_optional_reasoning_profile(raw_value)

    def resolve(
        self,
        *,
        request: ThinkingRequest,
        layers: ThinkingResolutionInput,
    ) -> ThinkingResolved:
        return resolve_thinking(request=request, layers=layers)

    def resolve_mode_aware(
        self,
        *,
        request: ThinkingRequest,
        layers: ThinkingResolutionInput,
        mode_policy: ModeThinkingPolicy | None,
        mode_name: str | None = None,
    ) -> ThinkingResolved:
        return resolve_mode_aware_thinking(
            request=request,
            layers=layers,
            mode_policy=mode_policy,
            mode_name=mode_name,
        )

    def build_provider_metadata(
        self,
        *,
        resolved: ThinkingResolved,
    ) -> dict[str, str]:
        metadata: dict[str, str] = {
            THINKING_METADATA_REASONING_PROFILE: resolved.reasoning_profile,
            THINKING_METADATA_SOURCE_LAYER: resolved.source_layer,
            THINKING_METADATA_SUPPORTED: str(bool(resolved.supported)).lower(),
        }
        if resolved.requested_profile is not None:
            metadata[THINKING_METADATA_REQUESTED_PROFILE] = resolved.requested_profile
        if resolved.provider_effort is not None:
            metadata[THINKING_METADATA_PROVIDER_EFFORT] = resolved.provider_effort
            metadata["thinking"] = resolved.provider_effort
        if resolved.degraded_reason:
            metadata[THINKING_METADATA_DEGRADED_REASON] = resolved.degraded_reason
        if resolved.degraded_reasons:
            metadata["thinking_degraded_reasons"] = ",".join(resolved.degraded_reasons)
        if resolved.provider_name:
            metadata[THINKING_METADATA_PROVIDER] = resolved.provider_name
        if resolved.model_name:
            metadata[THINKING_METADATA_MODEL] = resolved.model_name
        if resolved.mode_name:
            metadata["thinking_mode_name"] = resolved.mode_name
        if resolved.mode_default_profile is not None:
            metadata["thinking_mode_default_profile"] = resolved.mode_default_profile
        if resolved.mode_allowed_profiles:
            metadata["thinking_mode_allowed_profiles"] = ",".join(
                resolved.mode_allowed_profiles
            )
        if resolved.mode_request_override_allowed is not None:
            metadata["thinking_mode_request_override_allowed"] = str(
                bool(resolved.mode_request_override_allowed)
            ).lower()
        return metadata

    def build_context_hints(
        self,
        *,
        resolved: ThinkingResolved,
    ) -> dict[str, Any]:
        hints: dict[str, Any] = {
            THINKING_HINT_EFFECTIVE_PROFILE: resolved.reasoning_profile,
            THINKING_HINT_SOURCE_LAYER: resolved.source_layer,
            THINKING_HINT_SUPPORTED: bool(resolved.supported),
        }
        if resolved.requested_profile is not None:
            hints[THINKING_HINT_REQUESTED_PROFILE] = resolved.requested_profile
        if resolved.provider_effort is not None:
            hints[THINKING_HINT_PROVIDER_EFFORT] = resolved.provider_effort
        if resolved.degraded_reason:
            hints[THINKING_HINT_DEGRADED_REASON] = resolved.degraded_reason
        if resolved.degraded_reasons:
            hints["thinking_degraded_reasons"] = list(resolved.degraded_reasons)
        if resolved.mode_name:
            hints["thinking_mode_name"] = resolved.mode_name
        if resolved.mode_default_profile is not None:
            hints["thinking_mode_default_profile"] = resolved.mode_default_profile
        if resolved.mode_allowed_profiles:
            hints["thinking_mode_allowed_profiles"] = list(
                resolved.mode_allowed_profiles
            )
        if resolved.mode_request_override_allowed is not None:
            hints["thinking_mode_request_override_allowed"] = bool(
                resolved.mode_request_override_allowed
            )
        return hints
