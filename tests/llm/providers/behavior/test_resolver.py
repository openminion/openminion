from __future__ import annotations

import pytest

from openminion.modules.llm.providers.behavior import (
    BehaviorProfileRegistry,
    ProviderBehaviorProfile,
    RetryOverridePolicy,
    register_behavior_profile,
    resolve_behavior_profile,
)
from openminion.modules.llm.providers.normalization import (
    resolve_normalization_profile,
)
from openminion.modules.llm.providers.openai.request_compatibility import (
    resolve_openai_request_compat,
)
from openminion.modules.llm.providers.overrides.registry import (
    filter_provider_retry_overrides,
    provider_retry_overrides_disabled,
    resolve_provider_retry_override,
)
from openminion.modules.llm.providers.tool_calling.capabilities import (
    ToolSchemaCapability,
    build_tool_schema_name_map,
    resolve_tool_schema_capability,
)
from openminion.modules.llm.providers.tool_calling.registry import (
    resolve_fallback_parser_plugins,
)


class TestResolverDefaultLane:
    def test_openai_default_lane_resolves_to_default_profile(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="gpt-4",
            base_url="https://api.openai.com/v1",
        )

        assert isinstance(profile, ProviderBehaviorProfile)
        assert profile.profile_id == "default"

    def test_openrouter_lane_resolves_to_default_profile_at_pmbpi_01(self) -> None:
        profile = resolve_behavior_profile(
            provider="openrouter",
            model="anthropic/claude-3.5-sonnet",
            base_url="https://openrouter.ai/api/v1",
        )

        assert profile.profile_id == "default"

    def test_anthropic_lane_resolves_to_default_profile(self) -> None:
        profile = resolve_behavior_profile(
            provider="anthropic",
            model="claude-3-5-sonnet-20241022",
            base_url="https://api.anthropic.com",
        )

        assert profile.profile_id == "default"


class TestResolverMinimaxLane:
    def test_minimax_official_endpoint_resolves_to_minimax_openai_compat(
        self,
    ) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="MiniMax-M2.7",
            base_url="https://api.minimax.io/v1",
        )

        assert profile.profile_id == "minimax_openai_compat"

    def test_minimax_dashscope_endpoint_resolves_to_minimax_openai_compat(
        self,
    ) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="MiniMax-M2.7",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

        assert profile.profile_id == "minimax_openai_compat"

    def test_minimax_model_without_minimax_endpoint_resolves_to_default(
        self,
    ) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="MiniMax-M2.7",
            base_url="https://api.openai.com/v1",
        )

        assert profile.profile_id == "default"

    def test_minimax_endpoint_without_minimax_model_resolves_to_default(
        self,
    ) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="gpt-4",
            base_url="https://api.minimax.io/v1",
        )

        assert profile.profile_id == "default"


class TestResolverRequestCompatField:
    def test_default_lane_sets_default_request_dialect(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="gpt-4",
            base_url="https://api.openai.com/v1",
        )

        compat = resolve_openai_request_compat(request_dialect=profile.request_dialect)
        assert profile.request_dialect == "openai_default"
        assert compat.profile_id == "openai_default"

    def test_minimax_lane_sets_minimax_request_dialect(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="MiniMax-M2.7",
            base_url="https://api.minimax.io/v1",
        )

        compat = resolve_openai_request_compat(request_dialect=profile.request_dialect)
        assert profile.request_dialect == "minimax_openai_compat"
        assert compat.profile_id == "minimax_openai_compat"
        assert compat.retry_empty_payload_once is True

    def test_profile_driven_request_compat_matches_direct_resolution(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="MiniMax-M2.7",
            base_url="https://api.minimax.io/v1",
        )

        via_profile = resolve_openai_request_compat(
            provider_identity=(
                profile.provider_identity.as_metadata()
                if profile.provider_identity is not None
                else None
            ),
            request_dialect=profile.request_dialect,
        )
        direct = resolve_openai_request_compat(
            provider_identity=(
                profile.provider_identity.as_metadata()
                if profile.provider_identity is not None
                else None
            ),
        )

        assert via_profile == direct


class TestResolverProviderIdentity:
    def test_partial_explicit_provider_identity_fills_from_heuristic(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="MiniMax-M2.7",
            base_url="https://api.minimax.io/v1",
            provider_identity={"service_vendor": "minimax"},
        )

        assert profile.provider_identity is not None
        assert profile.provider_identity.as_metadata() == {
            "transport_adapter": "openai_chat",
            "wire_protocol_family": "openai_chat_completions",
            "service_vendor": "minimax",
            "model_family": "minimax",
        }
        assert profile.provider_identity_inferred_fields == (
            "transport_adapter",
            "wire_protocol_family",
            "model_family",
        )
        assert profile.provider_identity_overridden_fields == ()

    def test_explicit_provider_identity_overrides_heuristic_and_changes_lane(
        self,
    ) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="MiniMax-M2.7",
            base_url="https://api.minimax.io/v1",
            provider_identity={
                "transport_adapter": "openai_chat",
                "wire_protocol_family": "openai_chat_completions",
                "service_vendor": "custom-proxy",
                "model_family": "minimax",
            },
        )

        assert profile.request_dialect == "openai_default"
        assert profile.provider_identity is not None
        assert profile.provider_identity.service_vendor == "custom-proxy"
        assert profile.heuristic_provider_identity is not None
        assert profile.heuristic_provider_identity.service_vendor == "minimax"
        assert profile.provider_identity_overridden_fields == ("service_vendor",)


class TestResolverProviderGate:
    def test_minimax_model_on_minimax_endpoint_but_not_openai_provider_stays_default(
        self,
    ) -> None:
        profile = resolve_behavior_profile(
            provider="openrouter",
            model="MiniMax-M2.7",
            base_url="https://openrouter.ai/api/v1",
        )

        assert profile.profile_id == "default"


class TestResolverInputs:
    @pytest.mark.parametrize(
        "provider,model,base_url",
        [
            ("", "", ""),
            ("openai", "", ""),
            ("OPENAI", "GPT-4", "HTTPS://API.OPENAI.COM/V1"),
        ],
    )
    def test_resolver_is_total_functional_on_edge_inputs(
        self,
        provider: str,
        model: str,
        base_url: str,
    ) -> None:
        profile = resolve_behavior_profile(
            provider=provider,
            model=model,
            base_url=base_url,
        )

        assert isinstance(profile, ProviderBehaviorProfile)
        assert profile.profile_id  # non-empty string

    def test_resolver_accepts_optional_metadata_without_consuming_it(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="gpt-4",
            base_url="https://api.openai.com/v1",
            metadata={"phase": "decide", "thinking": True},
        )

        assert profile.profile_id == "default"


class TestRegistryIsolation:
    def test_caller_can_pass_a_custom_registry(self) -> None:
        custom = BehaviorProfileRegistry()
        custom.register(ProviderBehaviorProfile(profile_id="test_lane"))

        profile = resolve_behavior_profile(
            provider="openai",
            model="gpt-4",
            base_url="https://api.openai.com/v1",
            registry=custom,
        )

        assert profile.profile_id == "default"

    def test_register_behavior_profile_updates_default_registry(self) -> None:
        register_behavior_profile(
            ProviderBehaviorProfile(profile_id="test_pmbpi_01_seed")
        )

        from openminion.modules.llm.providers.behavior import default_registry

        assert "test_pmbpi_01_seed" in default_registry.ids()


class TestResolverCapabilityField:
    def test_default_lane_resolves_to_identity_capability(self) -> None:
        profile = resolve_behavior_profile(
            provider="anthropic",
            model="claude-3-5-sonnet-20241022",
            base_url="https://api.anthropic.com",
        )

        assert isinstance(profile.tool_schema_capability, ToolSchemaCapability)
        assert profile.tool_schema_capability.id == "identity"
        assert (
            profile.tool_schema_capability.requires_external_name_normalization is False
        )

    def test_openai_lane_resolves_to_openai_dialect_safe_names_capability(
        self,
    ) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="gpt-4",
            base_url="https://api.openai.com/v1",
        )

        assert profile.tool_schema_capability.id == "openai_dialect_safe_names"
        assert (
            profile.tool_schema_capability.requires_external_name_normalization is True
        )
        assert profile.tool_schema_capability.max_external_name_length == 128

    def test_openrouter_lane_resolves_to_openai_dialect_safe_names_capability(
        self,
    ) -> None:
        profile = resolve_behavior_profile(
            provider="openrouter",
            model="anthropic/claude-3.5-sonnet",
            base_url="https://openrouter.ai/api/v1",
        )

        assert profile.tool_schema_capability.id == "openai_dialect_safe_names"
        assert (
            profile.tool_schema_capability.requires_external_name_normalization is True
        )

    def test_profile_capability_matches_direct_call_for_default_lane(self) -> None:
        profile = resolve_behavior_profile(
            provider="anthropic",
            model="claude-3-5-sonnet-20241022",
        )
        direct = resolve_tool_schema_capability(
            provider_name="anthropic",
            model_name="claude-3-5-sonnet-20241022",
        )

        assert profile.tool_schema_capability == direct

    def test_profile_capability_matches_direct_call_for_openai_lane(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="gpt-4",
            base_url="https://api.openai.com/v1",
        )
        direct = resolve_tool_schema_capability(
            provider_name="openai",
            model_name="gpt-4",
        )

        assert profile.tool_schema_capability == direct

    def test_profile_capability_matches_direct_call_for_minimax_lane(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="MiniMax-M2.7",
            base_url="https://api.minimax.io/v1",
        )
        direct = resolve_tool_schema_capability(
            provider_name="openai",
            model_name="MiniMax-M2.7",
        )

        assert profile.profile_id == "minimax_openai_compat"
        assert profile.tool_schema_capability == direct
        assert profile.tool_schema_capability.id == "openai_dialect_safe_names"


class TestBuildToolSchemaNameMapProfileParameter:
    def test_no_capability_argument_falls_back_to_direct_resolution(self) -> None:
        # Pre-PMBPI-03 call shape: only provider_name + model_name.
        # Function resolves the capability internally.
        name_map = build_tool_schema_name_map(
            tools=[],
            provider_name="openai",
            model_name="gpt-4",
        )

        assert name_map.capability.id == "openai_dialect_safe_names"

    def test_explicit_capability_argument_overrides_direct_resolution(self) -> None:
        # Caller passes an explicit capability (typically from the
        # already-resolved profile) instead of triggering a second
        # provider/model resolution.
        explicit_capability = ToolSchemaCapability(
            id="test_override",
            requires_external_name_normalization=False,
        )
        name_map = build_tool_schema_name_map(
            tools=[],
            provider_name="openai",
            model_name="gpt-4",
            capability=explicit_capability,
        )

        assert name_map.capability.id == "test_override"
        assert name_map.capability is explicit_capability

    def test_profile_driven_path_matches_direct_call_path(self) -> None:
        # The PMBPI-03 no-behavior-change proof: same inputs through
        # either path produce identical name maps.
        profile = resolve_behavior_profile(
            provider="openai",
            model="gpt-4",
            base_url="https://api.openai.com/v1",
        )

        name_map_via_profile = build_tool_schema_name_map(
            tools=[],
            provider_name="openai",
            model_name="gpt-4",
            capability=profile.tool_schema_capability,
        )
        name_map_via_direct = build_tool_schema_name_map(
            tools=[],
            provider_name="openai",
            model_name="gpt-4",
        )

        assert name_map_via_profile.capability == name_map_via_direct.capability


class TestResolverRetryOverridePolicy:
    def test_openai_lane_carries_openai_applicable_overrides(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="gpt-4",
            base_url="https://api.openai.com/v1",
        )

        assert isinstance(profile.retry_override_policy, RetryOverridePolicy)
        assert profile.retry_override_policy.disabled is False
        # The openai_structured_thinking_tool_choice_retry override
        # at least this override.
        override_ids = {
            o.override_id for o in profile.retry_override_policy.applicable_overrides
        }
        assert "openai_structured_thinking_tool_choice_retry" in override_ids

    def test_openrouter_lane_carries_openrouter_applicable_overrides(self) -> None:
        profile = resolve_behavior_profile(
            provider="openrouter",
            model="anthropic/claude-3.5-sonnet",
            base_url="https://openrouter.ai/api/v1",
        )

        override_ids = {
            o.override_id for o in profile.retry_override_policy.applicable_overrides
        }
        # The openrouter_glm_minimax_tool_choice_required_retry override
        # admits provider_names=("openrouter",).
        assert "openrouter_glm_minimax_tool_choice_required_retry" in override_ids

    def test_anthropic_lane_carries_no_applicable_overrides(self) -> None:
        # No override in `_PROVIDER_RETRY_OVERRIDES` admits provider="anthropic",
        profile = resolve_behavior_profile(
            provider="anthropic",
            model="claude-3-5-sonnet-20241022",
            base_url="https://api.anthropic.com",
        )

        assert profile.retry_override_policy.disabled is False
        assert profile.retry_override_policy.applicable_overrides == ()

    def test_env_disable_hook_propagates_to_policy(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="gpt-4",
            base_url="https://api.openai.com/v1",
            env={"OPENMINION_DISABLE_PROVIDER_OVERRIDES": "1"},
        )

        assert profile.retry_override_policy.disabled is True
        assert profile.retry_override_policy.disabled_reason
        # When disabled, applicable_overrides stays empty even though
        assert profile.retry_override_policy.applicable_overrides == ()

    def test_metadata_disable_hook_propagates_to_policy(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="gpt-4",
            base_url="https://api.openai.com/v1",
            metadata={"provider_override_mode": "disabled"},
        )

        assert profile.retry_override_policy.disabled is True
        assert profile.retry_override_policy.applicable_overrides == ()

    def test_metadata_disable_truthy_value_propagates_to_policy(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="gpt-4",
            base_url="https://api.openai.com/v1",
            metadata={"disable_provider_overrides": "true"},
        )

        assert profile.retry_override_policy.disabled is True

    def test_default_lane_marker_for_tool_choice_policy(self) -> None:
        # PMBPI-04 light-touch: tool_choice_policy carries a
        profile = resolve_behavior_profile(
            provider="openai",
            model="gpt-4",
            base_url="https://api.openai.com/v1",
        )

        assert profile.tool_choice_policy == "default"

    def test_minimax_lane_marker_for_tool_choice_policy(self) -> None:
        # MiniMax-over-OpenAI-dialect → "minimax_openai_compat".
        profile = resolve_behavior_profile(
            provider="openai",
            model="MiniMax-M2.7",
            base_url="https://api.minimax.io/v1",
        )

        assert profile.tool_choice_policy == "minimax_openai_compat"


class TestResolverNormalizationAndParserFields:
    def test_openrouter_lane_carries_direct_normalization_profile(self) -> None:
        profile = resolve_behavior_profile(
            provider="openrouter",
            model="openrouter/oss20b",
            base_url="https://openrouter.ai/api/v1",
        )

        direct = resolve_normalization_profile(
            provider_name="openrouter",
            model_name="openrouter/oss20b",
        )
        assert profile.normalization_profile == direct
        assert profile.normalization_profile.name == "openrouter-oss"

    def test_minimax_lane_carries_structured_parser_policy(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="MiniMax-M2.7",
            base_url="https://api.minimax.io/v1",
        )

        direct = resolve_fallback_parser_plugins(
            provider_name="openai",
            model_name="MiniMax-M2.7",
            fallback_parser_policy="structured",
        )
        assert profile.fallback_parser_policy == "structured"
        assert profile.parser_plugin_selection == direct
        assert "minimax_xml" in profile.parser_plugin_selection
        assert "minimax_bracket" in profile.parser_plugin_selection

    def test_openrouter_default_lane_carries_openrouter_envelope_parser(self) -> None:
        profile = resolve_behavior_profile(
            provider="openrouter",
            model="openai/gpt-4.1-mini",
            base_url="https://openrouter.ai/api/v1",
        )

        assert profile.fallback_parser_policy == "full"
        assert "openrouter_envelope" in profile.parser_plugin_selection


class TestResolveProviderRetryOverrideWithPolicy:
    def test_no_policy_argument_falls_back_to_direct_resolution(self) -> None:
        # Pre-PMBPI-04 call shape: function evaluates disable hooks
        # and iterates the full table itself.
        resolution = resolve_provider_retry_override(
            provider_name="openai",
            model_name="gpt-4",
            purpose="decide",
            thinking="enabled",
            tool_choice={"type": "function", "function": {"name": "submit_output"}},
            tool_names=["submit_output"],
        )

        assert resolution.matched is True
        assert resolution.override_id == "openai_structured_thinking_tool_choice_retry"

    def test_policy_driven_path_matches_direct_call_for_matching_inputs(
        self,
    ) -> None:
        # Build the policy the way the resolver does (provider-filtered).
        policy = RetryOverridePolicy(
            disabled=False,
            applicable_overrides=filter_provider_retry_overrides("openai"),
        )

        resolution_via_policy = resolve_provider_retry_override(
            provider_name="openai",
            model_name="gpt-4",
            purpose="decide",
            thinking="enabled",
            tool_choice={"type": "function", "function": {"name": "submit_output"}},
            tool_names=["submit_output"],
            policy=policy,
        )
        resolution_via_direct = resolve_provider_retry_override(
            provider_name="openai",
            model_name="gpt-4",
            purpose="decide",
            thinking="enabled",
            tool_choice={"type": "function", "function": {"name": "submit_output"}},
            tool_names=["submit_output"],
        )

        assert resolution_via_policy.matched == resolution_via_direct.matched
        assert resolution_via_policy.override_id == resolution_via_direct.override_id
        assert (
            resolution_via_policy.retry_tool_choice
            == resolution_via_direct.retry_tool_choice
        )

    def test_policy_disabled_short_circuits_match(self) -> None:
        # Rollback assertion (spec acceptance #4): when the policy's
        # disable flag is set, no override matches even if call-time
        # inputs would otherwise match.
        policy = RetryOverridePolicy(
            disabled=True,
            disabled_reason="test disable",
            applicable_overrides=filter_provider_retry_overrides("openai"),
        )

        resolution = resolve_provider_retry_override(
            provider_name="openai",
            model_name="gpt-4",
            purpose="decide",
            thinking="enabled",
            tool_choice={"type": "function", "function": {"name": "submit_output"}},
            tool_names=["submit_output"],
            policy=policy,
        )

        assert resolution.matched is False
        assert resolution.disabled is True
        assert resolution.disabled_reason == "test disable"

    def test_policy_with_empty_applicable_overrides_yields_no_match(self) -> None:
        # The non-openai/openrouter lanes resolve to empty
        # applicable_overrides; the call-site resolution should
        # therefore find no match.
        policy = RetryOverridePolicy(
            disabled=False,
            applicable_overrides=(),
        )

        resolution = resolve_provider_retry_override(
            provider_name="anthropic",
            model_name="claude-3-5-sonnet-20241022",
            purpose="decide",
            thinking="enabled",
            tool_choice={"type": "function", "function": {"name": "submit_output"}},
            tool_names=["submit_output"],
            policy=policy,
        )

        assert resolution.matched is False
        assert resolution.disabled is False

    def test_full_profile_path_equivalent_to_direct_for_openai_matching_call(
        self,
    ) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="gpt-4",
            base_url="https://api.openai.com/v1",
        )

        via_profile = resolve_provider_retry_override(
            provider_name="openai",
            model_name="gpt-4",
            purpose="decide",
            thinking="enabled",
            tool_choice={"type": "function", "function": {"name": "submit_output"}},
            tool_names=["submit_output"],
            policy=profile.retry_override_policy,
        )
        direct = resolve_provider_retry_override(
            provider_name="openai",
            model_name="gpt-4",
            purpose="decide",
            thinking="enabled",
            tool_choice={"type": "function", "function": {"name": "submit_output"}},
            tool_names=["submit_output"],
        )

        assert via_profile.matched == direct.matched
        assert via_profile.override_id == direct.override_id


class TestProviderRetryOverridesDisabledHelper:
    def test_no_disable_signal_returns_false(self) -> None:
        disabled, reason = provider_retry_overrides_disabled()

        assert disabled is False
        assert reason == ""

    def test_env_var_set_returns_true_with_reason(self) -> None:
        disabled, reason = provider_retry_overrides_disabled(
            env={"OPENMINION_DISABLE_PROVIDER_OVERRIDES": "1"}
        )

        assert disabled is True
        assert reason

    def test_metadata_mode_disabled_returns_true(self) -> None:
        disabled, reason = provider_retry_overrides_disabled(
            metadata={"provider_override_mode": "disabled"}
        )

        assert disabled is True
        assert reason


class TestFilterProviderRetryOverridesHelper:
    def test_openai_provider_yields_at_least_openai_override(self) -> None:
        overrides = filter_provider_retry_overrides("openai")

        assert any(o.override_id.startswith("openai_") for o in overrides)

    def test_openrouter_provider_yields_at_least_openrouter_override(self) -> None:
        overrides = filter_provider_retry_overrides("openrouter")

        assert any(o.override_id.startswith("openrouter_") for o in overrides)

    def test_unknown_provider_yields_empty_tuple(self) -> None:
        overrides = filter_provider_retry_overrides("unknown-provider")

        assert overrides == ()

    def test_provider_name_lowercased_for_match(self) -> None:
        # `filter_provider_retry_overrides` normalizes the input to
        # lowercase; "OPENAI" should match the same overrides as "openai".
        upper = filter_provider_retry_overrides("OPENAI")
        lower = filter_provider_retry_overrides("openai")

        assert upper == lower
