"""Typed reasoning-profile config normalization and layer precedence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

REASONING_PROFILE_OFF = "off"
REASONING_PROFILE_MINIMAL = "minimal"
REASONING_PROFILE_DETAILED = "detailed"
REASONING_PROFILES = ("off", "minimal", "detailed")

REASONING_SOURCE_CAPABILITY_DEFINITION = "capability_definition"
REASONING_SOURCE_SYSTEM_RUNTIME = "system_runtime"
REASONING_SOURCE_AGENT_RUNTIME = "agent_runtime"
REASONING_SOURCE_INVOCATION_OVERRIDE = "invocation_override"

_OFF_ALIASES = frozenset({"0", "disabled", "false", "none", "no", "off"})
_MINIMAL_ALIASES = frozenset({"default", "light", "low", "min", "minimal", "normal"})
_DETAILED_ALIASES = frozenset(
    {"deep", "detailed", "full", "hard", "harder", "high", "max", "verbose"}
)
_KNOWN_PROFILE_TOKENS = _OFF_ALIASES | _MINIMAL_ALIASES | _DETAILED_ALIASES
_UNKNOWN_PROFILE_REASON = "unknown_reasoning_profile_normalized"


def normalize_optional_reasoning_profile(raw_value: Any) -> str | None:
    token = str(raw_value or "").strip().lower()
    if not token:
        return None
    if token in _OFF_ALIASES:
        return REASONING_PROFILE_OFF
    if token in _DETAILED_ALIASES:
        return REASONING_PROFILE_DETAILED
    return REASONING_PROFILE_MINIMAL


def reasoning_profile_was_unknown(raw_value: Any) -> bool:
    token = str(raw_value or "").strip().lower()
    return bool(token) and token not in _KNOWN_PROFILE_TOKENS


@dataclass(frozen=True, slots=True)
class RuntimeReasoningConfigResolution:
    requested_profile: str | None
    reasoning_profile: str
    source_layer: str
    system_profile: str | None
    agent_profile: str | None
    provider_name: str = ""
    model_name: str = ""
    unknown_request_profile: bool = False

    def diagnostics_payload(self) -> dict[str, Any]:
        provider_effort: str | None = self.reasoning_profile
        if provider_effort == REASONING_PROFILE_OFF:
            provider_effort = None
        payload: dict[str, Any] = {
            "requested_profile": self.requested_profile,
            "reasoning_profile": self.reasoning_profile,
            "provider_effort": provider_effort,
            "source_layer": self.source_layer,
            "supported": True,
            "degraded_reason": None,
            "mode_request_override_allowed": True,
        }
        if self.unknown_request_profile:
            payload["degraded_reason"] = _UNKNOWN_PROFILE_REASON
            payload["degraded_reasons"] = [_UNKNOWN_PROFILE_REASON]
        for key, value in (
            ("provider", self.provider_name),
            ("model", self.model_name),
            ("system_profile", self.system_profile),
            ("agent_profile", self.agent_profile),
            ("request_override_profile", self.requested_profile),
        ):
            if value:
                payload[key] = value
        return payload


def resolve_runtime_reasoning_config(
    *,
    code_default_profile: str,
    system_profile: str | None,
    agent_profile: str | None,
    invocation_requested_profile: str | None,
    provider_name: str | None = None,
    model_name: str | None = None,
) -> RuntimeReasoningConfigResolution:
    normalize = normalize_optional_reasoning_profile
    requested = normalize(invocation_requested_profile)
    system = normalize(system_profile)
    agent = normalize(agent_profile)
    effective = normalize(code_default_profile) or REASONING_PROFILE_MINIMAL
    source = REASONING_SOURCE_CAPABILITY_DEFINITION
    for layer, profile in (
        (REASONING_SOURCE_SYSTEM_RUNTIME, system),
        (REASONING_SOURCE_AGENT_RUNTIME, agent),
        (REASONING_SOURCE_INVOCATION_OVERRIDE, requested),
    ):
        if profile is not None:
            effective, source = profile, layer
    return RuntimeReasoningConfigResolution(
        requested_profile=requested,
        reasoning_profile=effective,
        source_layer=source,
        system_profile=system,
        agent_profile=agent,
        provider_name=str(provider_name or "").strip(),
        model_name=str(model_name or "").strip(),
        unknown_request_profile=reasoning_profile_was_unknown(
            invocation_requested_profile
        ),
    )
