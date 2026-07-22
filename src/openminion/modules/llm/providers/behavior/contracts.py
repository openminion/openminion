"""Typed contracts for provider behavior profiles."""

from dataclasses import dataclass, field
from typing import Any
from collections.abc import Mapping

from openminion.modules.llm.providers.behavior.constants import (
    DEFAULT_FALLBACK_PARSER_POLICY,
    DEFAULT_REQUEST_DIALECT,
    DEFAULT_TOOL_CHOICE_POLICY,
)
from openminion.modules.llm.providers.normalization import (
    ProviderResponseNormalizationProfile,
    resolve_normalization_profile,
)
from openminion.modules.llm.providers.overrides.registry import ProviderRetryOverride
from openminion.modules.llm.providers.tool_calling.capabilities import (
    ToolSchemaCapability,
)


def _default_normalization_profile() -> ProviderResponseNormalizationProfile:
    return resolve_normalization_profile()


@dataclass(frozen=True)
class ProviderIdentity:
    """Resolved PTVC identity facts for one provider call."""

    transport_adapter: str
    wire_protocol_family: str
    service_vendor: str
    model_family: str
    upstream_vendor_hint: str = ""

    def as_metadata(self) -> dict[str, str]:
        payload = {
            "transport_adapter": self.transport_adapter,
            "wire_protocol_family": self.wire_protocol_family,
            "service_vendor": self.service_vendor,
            "model_family": self.model_family,
        }
        if self.upstream_vendor_hint:
            payload["upstream_vendor_hint"] = self.upstream_vendor_hint
        return payload

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any] | None,
    ) -> "ProviderIdentity | None":
        if payload is None:
            return None
        transport_adapter = str(payload.get("transport_adapter") or "").strip()
        wire_protocol_family = str(payload.get("wire_protocol_family") or "").strip()
        service_vendor = str(payload.get("service_vendor") or "").strip()
        model_family = str(payload.get("model_family") or "").strip()
        upstream_vendor_hint = str(payload.get("upstream_vendor_hint") or "").strip()
        if not (
            transport_adapter
            and wire_protocol_family
            and service_vendor
            and model_family
        ):
            return None
        return cls(
            transport_adapter=transport_adapter,
            wire_protocol_family=wire_protocol_family,
            service_vendor=service_vendor,
            model_family=model_family,
            upstream_vendor_hint=upstream_vendor_hint,
        )


@dataclass(frozen=True)
class RetryOverridePolicy:
    """Retry-override candidates and disable state for one provider call."""

    disabled: bool = False
    disabled_reason: str = ""
    applicable_overrides: tuple[ProviderRetryOverride, ...] = ()


@dataclass(frozen=True)
class ProviderBehaviorProfile:
    """Resolved provider behavior choices for one provider call."""

    # Identity / observability
    profile_id: str = "default"
    provider_identity: ProviderIdentity | None = None
    heuristic_provider_identity: ProviderIdentity | None = None
    provider_identity_inferred_fields: tuple[str, ...] = field(default_factory=tuple)
    provider_identity_overridden_fields: tuple[str, ...] = field(default_factory=tuple)

    # Selection seams resolved before request formation.
    request_dialect: str = DEFAULT_REQUEST_DIALECT
    tool_call_dialect: str = "native"
    tool_schema_capability: ToolSchemaCapability = field(
        default_factory=ToolSchemaCapability
    )
    tool_choice_policy: str = DEFAULT_TOOL_CHOICE_POLICY
    retry_override_policy: RetryOverridePolicy = field(
        default_factory=RetryOverridePolicy
    )
    normalization_profile: ProviderResponseNormalizationProfile = field(
        default_factory=_default_normalization_profile
    )
    fallback_parser_policy: str = DEFAULT_FALLBACK_PARSER_POLICY
    parser_plugin_selection: tuple[str, ...] = field(default_factory=tuple)
    thinking_policy: str = "default"

    # Operator-facing labels for trace/debug and telemetry (spec §9)
    telemetry_labels: tuple[str, ...] = field(default_factory=tuple)
