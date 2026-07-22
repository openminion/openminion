from dataclasses import dataclass, field
from typing import Any
from collections.abc import Mapping

from .constants import (
    THINKING_REASONING_PROFILE_MINIMAL,
)


@dataclass(frozen=True, slots=True)
class ThinkingRequest:
    purpose: str | None = None
    requested_profile: str | None = None
    provider: str | None = None
    model: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModeThinkingPolicy:
    default_reasoning_profile: str | None = None
    allowed_reasoning_profiles: tuple[str, ...] | None = None
    allow_request_override: bool = True


@dataclass(frozen=True, slots=True)
class ThinkingResolutionInput:
    code_default_profile: str = THINKING_REASONING_PROFILE_MINIMAL
    system_profile: str | None = None
    agent_profile: str | None = None
    request_profile: str | None = None


@dataclass(frozen=True, slots=True)
class ThinkingResolved:
    requested_profile: str | None
    reasoning_profile: str
    provider_effort: str | None
    source_layer: str
    supported: bool
    degraded_reason: str | None = None
    degraded_reasons: tuple[str, ...] = ()
    provider_name: str = ""
    model_name: str = ""
    system_profile: str | None = None
    agent_profile: str | None = None
    request_override_profile: str | None = None
    mode_name: str | None = None
    mode_default_profile: str | None = None
    mode_allowed_profiles: tuple[str, ...] = ()
    mode_request_override_allowed: bool | None = None

    def diagnostics_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "requested_profile": self.requested_profile,
            "reasoning_profile": self.reasoning_profile,
            "provider_effort": self.provider_effort,
            "source_layer": self.source_layer,
            "supported": self.supported,
            "degraded_reason": self.degraded_reason,
        }
        if self.degraded_reasons:
            payload["degraded_reasons"] = list(self.degraded_reasons)
        if self.provider_name:
            payload["provider"] = self.provider_name
        if self.model_name:
            payload["model"] = self.model_name
        if self.system_profile is not None:
            payload["system_profile"] = self.system_profile
        if self.agent_profile is not None:
            payload["agent_profile"] = self.agent_profile
        if self.request_override_profile is not None:
            payload["request_override_profile"] = self.request_override_profile
        if self.mode_name:
            payload["mode_name"] = self.mode_name
        if self.mode_default_profile is not None:
            payload["mode_default_profile"] = self.mode_default_profile
        if self.mode_allowed_profiles:
            payload["mode_allowed_profiles"] = list(self.mode_allowed_profiles)
        if self.mode_request_override_allowed is not None:
            payload["mode_request_override_allowed"] = bool(
                self.mode_request_override_allowed
            )
        return payload


@dataclass(frozen=True, slots=True)
class ThinkingRuntimeDiagnostics:
    code_default_profile: str
    system_profile: str | None
    agent_profile: str | None
    invocation_requested_profile: str | None
    effective: ThinkingResolved
