from __future__ import annotations

from openminion.modules.llm.providers.behavior import (
    ProviderIdentity,
    resolve_behavior_profile,
)


def test_typed_identity_construction_is_a_bounded_per_lane_surface():
    acmecloud_identity = ProviderIdentity(
        transport_adapter="openai_chat",
        wire_protocol_family="openai_chat_completions",
        service_vendor="acmecloud",
        model_family="acme",
    )
    assert acmecloud_identity.service_vendor == "acmecloud"
    assert acmecloud_identity.model_family == "acme"
    metadata = acmecloud_identity.as_metadata()
    assert metadata["service_vendor"] == "acmecloud"
    assert metadata["model_family"] == "acme"


def test_explicit_identity_override_does_not_require_shared_registry_edits():
    profile = resolve_behavior_profile(
        provider="openai",
        model="acme-flash",
        base_url="https://api.acmecloud.io/v1",
        provider_identity={
            "transport_adapter": "openai_chat",
            "wire_protocol_family": "openai_chat_completions",
            "service_vendor": "acmecloud",
            "model_family": "acme",
        },
    )
    assert profile.provider_identity is not None
    assert profile.provider_identity.service_vendor == "acmecloud"
    assert profile.provider_identity.model_family == "acme"


def test_profile_for_new_lane_carries_all_typed_selections():
    profile = resolve_behavior_profile(
        provider="openai",
        model="acme-flash",
        base_url="https://api.acmecloud.io/v1",
        provider_identity={
            "transport_adapter": "openai_chat",
            "wire_protocol_family": "openai_chat_completions",
            "service_vendor": "acmecloud",
            "model_family": "acme",
        },
    )
    assert profile.request_dialect
    assert profile.tool_choice_policy
    assert profile.fallback_parser_policy
    assert profile.normalization_profile is not None
    assert profile.tool_schema_capability is not None
    assert profile.retry_override_policy is not None
    assert profile.parser_plugin_selection is not None


def test_new_lane_does_not_accidentally_inherit_minimax_compat_settings():
    profile = resolve_behavior_profile(
        provider="openai",
        model="acme-flash",
        base_url="https://api.acmecloud.io/v1",
        provider_identity={
            "transport_adapter": "openai_chat",
            "wire_protocol_family": "openai_chat_completions",
            "service_vendor": "acmecloud",
            "model_family": "acme",
        },
    )
    assert profile.request_dialect != "minimax_openai_compat"


def test_bounded_extension_proof_artifact_exists():
    import os.path

    proof_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "..",
        "..",
        "docs",
        "discussions",
        "pbpgh-05-bounded-extension-proof-2026-05-27.md",
    )
    assert os.path.isfile(proof_path), (
        f"PBPGH-05 proof artifact missing at expected path: {proof_path}"
    )
