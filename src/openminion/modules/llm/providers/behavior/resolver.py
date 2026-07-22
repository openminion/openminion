"""Resolve one provider behavior profile per provider call."""

from dataclasses import replace
from typing import Any
from collections.abc import Mapping

from openminion.modules.llm.providers.behavior.constants import (
    DASHSCOPE_SERVICE_VENDOR,
    DEFAULT_FALLBACK_PARSER_POLICY,
    DEFAULT_REQUEST_DIALECT,
    DEFAULT_TOOL_CHOICE_POLICY,
    MINIMAX_MODEL_FAMILY,
    MINIMAX_OPENAI_COMPAT_REQUEST_DIALECT,
    MINIMAX_OPENAI_COMPAT_TOOL_CHOICE_POLICY,
    MINIMAX_SERVICE_VENDOR,
    OPENAI_CHAT_COMPLETIONS_WIRE_PROTOCOL_FAMILY,
    OPENAI_CHAT_TRANSPORT_ADAPTER,
    STRUCTURED_FALLBACK_PARSER_POLICY,
)
from openminion.modules.llm.providers.behavior.contracts import (
    ProviderBehaviorProfile,
    ProviderIdentity,
    RetryOverridePolicy,
)
from openminion.modules.llm.providers.behavior.registry import (
    BehaviorProfileRegistry,
    default_registry,
)
from openminion.modules.llm.providers.overrides.registry import (
    filter_provider_retry_overrides,
    provider_retry_overrides_disabled,
)
from openminion.modules.llm.providers.normalization import (
    resolve_normalization_profile,
)
from openminion.modules.llm.providers.tool_calling.registry import (
    resolve_fallback_parser_plugins,
)
from openminion.modules.llm.providers.tool_calling.capabilities import (
    resolve_tool_schema_capability,
)


def resolve_behavior_profile(
    *,
    provider: str,
    model: str,
    base_url: str = "",
    provider_identity: Mapping[str, Any] | ProviderIdentity | None = None,
    metadata: Mapping[str, Any] | None = None,
    env: Mapping[str, object] | None = None,
    registry: BehaviorProfileRegistry | None = None,
) -> ProviderBehaviorProfile:
    """Resolve a fresh behavior profile for one provider call."""

    active_registry = registry if registry is not None else default_registry

    model_name = str(model or "").strip().lower()
    endpoint = str(base_url or "").strip().lower()
    provider_name = str(provider or "").strip().lower()

    heuristic_identity = _resolve_heuristic_provider_identity(
        provider_name=provider_name,
        model_name=model_name,
        endpoint=endpoint,
    )
    resolved_identity, inferred_fields, overridden_fields = _resolve_provider_identity(
        explicit_identity=provider_identity,
        heuristic_identity=heuristic_identity,
    )
    compat_lane = _uses_minimax_openai_compat_lane(resolved_identity)

    template: ProviderBehaviorProfile | None = None
    request_dialect = DEFAULT_REQUEST_DIALECT
    tool_choice_policy = DEFAULT_TOOL_CHOICE_POLICY
    fallback_parser_policy = DEFAULT_FALLBACK_PARSER_POLICY
    if compat_lane:
        template = active_registry.get("minimax_openai_compat")
        request_dialect = MINIMAX_OPENAI_COMPAT_REQUEST_DIALECT
        tool_choice_policy = MINIMAX_OPENAI_COMPAT_TOOL_CHOICE_POLICY
        fallback_parser_policy = STRUCTURED_FALLBACK_PARSER_POLICY
    if template is None:
        template = active_registry.get("default")
    if template is None:
        template = ProviderBehaviorProfile()

    capability = resolve_tool_schema_capability(
        provider_name=provider,
        model_name=model,
    )
    normalization_profile = resolve_normalization_profile(
        provider_name=provider_name,
        model_name=model_name,
    )
    parser_plugin_selection = resolve_fallback_parser_plugins(
        provider_name=provider_name,
        model_name=model_name,
        fallback_parser_policy=fallback_parser_policy,
    )

    disabled, disabled_reason = provider_retry_overrides_disabled(
        metadata=metadata, env=env
    )
    if disabled:
        applicable_overrides: tuple = ()
    else:
        applicable_overrides = filter_provider_retry_overrides(provider_name)
    retry_override_policy = RetryOverridePolicy(
        disabled=disabled,
        disabled_reason=disabled_reason,
        applicable_overrides=applicable_overrides,
    )

    return replace(
        template,
        provider_identity=resolved_identity,
        heuristic_provider_identity=heuristic_identity,
        provider_identity_inferred_fields=inferred_fields,
        provider_identity_overridden_fields=overridden_fields,
        request_dialect=request_dialect,
        tool_schema_capability=capability,
        retry_override_policy=retry_override_policy,
        normalization_profile=normalization_profile,
        fallback_parser_policy=fallback_parser_policy,
        parser_plugin_selection=parser_plugin_selection,
        tool_choice_policy=tool_choice_policy,
    )


def _resolve_provider_identity(
    *,
    explicit_identity: Mapping[str, Any] | ProviderIdentity | None,
    heuristic_identity: ProviderIdentity | None,
) -> tuple[ProviderIdentity | None, tuple[str, ...], tuple[str, ...]]:
    if isinstance(explicit_identity, ProviderIdentity):
        return explicit_identity, (), ()

    explicit_payload = dict(explicit_identity or {})
    explicit_fields = {
        key: str(explicit_payload.get(key) or "").strip()
        for key in (
            "transport_adapter",
            "wire_protocol_family",
            "service_vendor",
            "model_family",
            "upstream_vendor_hint",
        )
    }
    if not any(explicit_fields.values()):
        return heuristic_identity, (), ()

    heuristic_payload = (
        heuristic_identity.as_metadata() if heuristic_identity is not None else {}
    )
    merged_payload: dict[str, str] = {}
    inferred_fields: list[str] = []
    overridden_fields: list[str] = []
    required_fields = (
        "transport_adapter",
        "wire_protocol_family",
        "service_vendor",
        "model_family",
    )
    for field_name in required_fields:
        explicit_value = explicit_fields[field_name]
        heuristic_value = str(heuristic_payload.get(field_name) or "").strip()
        if explicit_value:
            merged_payload[field_name] = explicit_value
            if heuristic_value and heuristic_value != explicit_value:
                overridden_fields.append(field_name)
        elif heuristic_value:
            merged_payload[field_name] = heuristic_value
            inferred_fields.append(field_name)
    explicit_upstream = explicit_fields["upstream_vendor_hint"]
    heuristic_upstream = str(
        heuristic_payload.get("upstream_vendor_hint") or ""
    ).strip()
    if explicit_upstream:
        merged_payload["upstream_vendor_hint"] = explicit_upstream
        if heuristic_upstream and heuristic_upstream != explicit_upstream:
            overridden_fields.append("upstream_vendor_hint")
    elif heuristic_upstream:
        merged_payload["upstream_vendor_hint"] = heuristic_upstream
        inferred_fields.append("upstream_vendor_hint")

    return (
        ProviderIdentity.from_mapping(merged_payload),
        tuple(inferred_fields),
        tuple(overridden_fields),
    )


def _resolve_heuristic_provider_identity(
    *,
    provider_name: str,
    model_name: str,
    endpoint: str,
) -> ProviderIdentity | None:
    """Resolve heuristic provider identity helper."""
    from openminion.modules.llm.config import resolve_provider_identity_translation

    translation = resolve_provider_identity_translation(
        provider_name,
        model=model_name,
        base_url=endpoint,
    )
    if not translation:
        return None
    return ProviderIdentity(
        transport_adapter=translation["transport_adapter"],
        wire_protocol_family=translation["wire_protocol_family"],
        service_vendor=translation["service_vendor"],
        model_family=translation["model_family"],
    )


def _uses_minimax_openai_compat_lane(identity: ProviderIdentity | None) -> bool:
    if identity is None:
        return False
    if identity.transport_adapter != OPENAI_CHAT_TRANSPORT_ADAPTER:
        return False
    if identity.wire_protocol_family != OPENAI_CHAT_COMPLETIONS_WIRE_PROTOCOL_FAMILY:
        return False
    if identity.model_family != MINIMAX_MODEL_FAMILY:
        return False
    return identity.service_vendor in {
        MINIMAX_SERVICE_VENDOR,
        DASHSCOPE_SERVICE_VENDOR,
    }
