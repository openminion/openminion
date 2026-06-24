from __future__ import annotations

import pytest

from openminion.modules.llm.providers.behavior import (
    ProviderBehaviorProfile,
    resolve_behavior_profile,
)


@pytest.fixture
def gpt4_profile() -> ProviderBehaviorProfile:
    return resolve_behavior_profile(provider="openai", model="gpt-4", base_url="")


@pytest.fixture
def minimax_profile() -> ProviderBehaviorProfile:
    return resolve_behavior_profile(
        provider="openai",
        model="minimax-m2",
        base_url="https://api.minimax.io/v1",
    )


@pytest.fixture
def dashscope_qwen_profile() -> ProviderBehaviorProfile:
    return resolve_behavior_profile(
        provider="openai",
        model="qwen-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )


def test_profile_carries_request_dialect(gpt4_profile):
    assert isinstance(gpt4_profile.request_dialect, str)
    assert gpt4_profile.request_dialect


def test_profile_carries_normalization_profile(gpt4_profile):
    assert gpt4_profile.normalization_profile is not None


def test_profile_carries_tool_schema_capability(gpt4_profile):
    assert gpt4_profile.tool_schema_capability is not None


def test_profile_carries_retry_override_policy(gpt4_profile):
    policy = gpt4_profile.retry_override_policy
    assert policy is not None
    assert hasattr(policy, "disabled")
    assert hasattr(policy, "applicable_overrides")


def test_profile_carries_tool_choice_policy(gpt4_profile):
    assert isinstance(gpt4_profile.tool_choice_policy, str)
    assert gpt4_profile.tool_choice_policy


def test_profile_carries_fallback_parser_policy(gpt4_profile):
    assert isinstance(gpt4_profile.fallback_parser_policy, str)
    assert gpt4_profile.fallback_parser_policy


def test_profile_carries_parser_plugin_selection(gpt4_profile):
    selection = gpt4_profile.parser_plugin_selection
    assert selection is not None


def test_profile_carries_provider_identity(gpt4_profile):
    identity = gpt4_profile.provider_identity
    assert identity is not None
    assert identity.transport_adapter == "openai_chat"
    assert identity.wire_protocol_family == "openai_chat_completions"


def test_minimax_compat_lane_differs_from_default(gpt4_profile, minimax_profile):
    assert (
        minimax_profile.request_dialect != gpt4_profile.request_dialect
        or minimax_profile.tool_choice_policy != gpt4_profile.tool_choice_policy
        or minimax_profile.fallback_parser_policy != gpt4_profile.fallback_parser_policy
    )


def test_dashscope_qwen_resolves_typed_identity(dashscope_qwen_profile):
    identity = dashscope_qwen_profile.provider_identity
    assert identity is not None
    assert identity.service_vendor == "dashscope"
    assert identity.model_family == "qwen"


def test_profile_is_immutable_dataclass(gpt4_profile):
    with pytest.raises(Exception):  # FrozenInstanceError
        gpt4_profile.request_dialect = "tampered"  # type: ignore[misc]


def test_resolve_behavior_profile_is_deterministic_for_same_inputs():
    profile_a = resolve_behavior_profile(provider="openai", model="gpt-4", base_url="")
    profile_b = resolve_behavior_profile(provider="openai", model="gpt-4", base_url="")
    assert profile_a.request_dialect == profile_b.request_dialect
    assert profile_a.tool_choice_policy == profile_b.tool_choice_policy
    assert profile_a.fallback_parser_policy == profile_b.fallback_parser_policy
    assert profile_a.provider_identity == profile_b.provider_identity


def test_all_six_selection_seams_resolved_in_one_call(gpt4_profile):
    seams = (
        gpt4_profile.request_dialect,
        gpt4_profile.tool_choice_policy,
        gpt4_profile.fallback_parser_policy,
        gpt4_profile.normalization_profile,
        gpt4_profile.tool_schema_capability,
        gpt4_profile.retry_override_policy,
        gpt4_profile.parser_plugin_selection,
        gpt4_profile.provider_identity,
    )
    assert all(seam is not None for seam in seams), (
        "PBPGH-02 invariant violated: not all selection seams resolved in one profile"
    )
