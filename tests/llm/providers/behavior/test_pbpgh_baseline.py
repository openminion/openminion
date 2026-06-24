from __future__ import annotations

import pytest

from openminion.modules.llm.config import (
    resolve_provider_identity_translation,
)
from openminion.modules.llm.providers.behavior.constants import (
    CLAUDE_MODEL_FAMILY,
    DASHSCOPE_SERVICE_VENDOR,
    DEFAULT_FALLBACK_PARSER_POLICY,
    DEFAULT_REQUEST_DIALECT,
    GPT_MODEL_FAMILY,
    MINIMAX_MODEL_FAMILY,
    MINIMAX_OPENAI_COMPAT_REQUEST_DIALECT,
    MINIMAX_SERVICE_VENDOR,
    OPENAI_CHAT_COMPLETIONS_WIRE_PROTOCOL_FAMILY,
    OPENAI_CHAT_TRANSPORT_ADAPTER,
    OPENAI_MODEL_FAMILY,
    OPENAI_SERVICE_VENDOR,
    STRUCTURED_FALLBACK_PARSER_POLICY,
)
from openminion.modules.llm.providers.behavior.resolver import (
    resolve_behavior_profile,
)
from openminion.modules.llm.providers.openai.request_compatibility import (
    resolve_openai_request_compat,
)
from openminion.modules.llm.providers.tool_calling.registry import (
    resolve_fallback_parser_plugins,
)


class TestIdentityTranslationInterplay:
    @pytest.mark.parametrize(
        "provider,model,base_url,expected_vendor,expected_family",
        [
            (
                "openai",
                "gpt-4",
                "",
                OPENAI_SERVICE_VENDOR,
                GPT_MODEL_FAMILY,
            ),
            (
                "openai",
                "minimax-m2-7",
                "https://api.minimax.io/v1",
                MINIMAX_SERVICE_VENDOR,
                MINIMAX_MODEL_FAMILY,
            ),
            (
                "openai",
                "qwen-plus",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                DASHSCOPE_SERVICE_VENDOR,
                "qwen",
            ),
            (
                "openai",
                "claude-sonnet-4",
                "",
                OPENAI_SERVICE_VENDOR,
                CLAUDE_MODEL_FAMILY,
            ),
            (
                "openai",
                "o3-mini",
                "",
                OPENAI_SERVICE_VENDOR,
                GPT_MODEL_FAMILY,
            ),
            (
                "openai",
                "unknown-model-name",
                "",
                OPENAI_SERVICE_VENDOR,
                OPENAI_MODEL_FAMILY,
            ),
        ],
    )
    def test_config_py_translation_matches_expected(
        self,
        provider: str,
        model: str,
        base_url: str,
        expected_vendor: str,
        expected_family: str,
    ) -> None:
        result = resolve_provider_identity_translation(
            provider, model=model, base_url=base_url
        )
        assert result["transport_adapter"] == OPENAI_CHAT_TRANSPORT_ADAPTER
        assert (
            result["wire_protocol_family"]
            == OPENAI_CHAT_COMPLETIONS_WIRE_PROTOCOL_FAMILY
        )
        assert result["service_vendor"] == expected_vendor
        assert result["model_family"] == expected_family

    @pytest.mark.parametrize(
        "provider,model,base_url,expected_vendor,expected_family",
        [
            (
                "openai",
                "gpt-4",
                "",
                OPENAI_SERVICE_VENDOR,
                GPT_MODEL_FAMILY,
            ),
            (
                "openai",
                "minimax-m2-7",
                "https://api.minimax.io/v1",
                MINIMAX_SERVICE_VENDOR,
                MINIMAX_MODEL_FAMILY,
            ),
            (
                "openai",
                "qwen-plus",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                DASHSCOPE_SERVICE_VENDOR,
                "qwen",
            ),
            (
                "openai",
                "claude-sonnet-4",
                "",
                OPENAI_SERVICE_VENDOR,
                CLAUDE_MODEL_FAMILY,
            ),
        ],
    )
    def test_resolver_py_heuristic_matches_config_py(
        self,
        provider: str,
        model: str,
        base_url: str,
        expected_vendor: str,
        expected_family: str,
    ) -> None:
        profile = resolve_behavior_profile(
            provider=provider, model=model, base_url=base_url
        )
        identity = profile.heuristic_provider_identity
        assert identity is not None
        assert identity.transport_adapter == OPENAI_CHAT_TRANSPORT_ADAPTER
        assert (
            identity.wire_protocol_family
            == OPENAI_CHAT_COMPLETIONS_WIRE_PROTOCOL_FAMILY
        )
        assert identity.service_vendor == expected_vendor
        assert identity.model_family == expected_family

        config_translation = resolve_provider_identity_translation(
            provider, model=model, base_url=base_url
        )
        assert identity.service_vendor == config_translation["service_vendor"]
        assert identity.model_family == config_translation["model_family"]

    def test_non_openai_provider_returns_no_identity(self) -> None:
        assert resolve_provider_identity_translation("anthropic", model="x") == {}
        profile = resolve_behavior_profile(provider="anthropic", model="x")
        assert profile.heuristic_provider_identity is None


class TestParserRoutingOutcomes:
    def test_default_provider_default_model_full_policy(self) -> None:
        plugins = resolve_fallback_parser_plugins(
            provider_name="openai",
            model_name="gpt-4",
            fallback_parser_policy=DEFAULT_FALLBACK_PARSER_POLICY,
        )
        assert len(plugins) == 5
        assert "minimax_xml" in plugins or any("minimax" in p for p in plugins)

    def test_minimax_model_full_policy(self) -> None:
        plugins = resolve_fallback_parser_plugins(
            provider_name="openai",
            model_name="minimax-m2-7",
            fallback_parser_policy=DEFAULT_FALLBACK_PARSER_POLICY,
        )
        assert len(plugins) == 5

    def test_structured_policy_returns_fewer_handlers(self) -> None:
        plugins = resolve_fallback_parser_plugins(
            provider_name="openai",
            model_name="minimax-m2-7",
            fallback_parser_policy=STRUCTURED_FALLBACK_PARSER_POLICY,
        )
        assert len(plugins) == 4

    def test_openrouter_provider_prepends_envelope_handler(self) -> None:
        plugins = resolve_fallback_parser_plugins(
            provider_name="openrouter",
            model_name="gpt-4",
            fallback_parser_policy=DEFAULT_FALLBACK_PARSER_POLICY,
        )
        assert len(plugins) >= 6
        assert plugins[0] != plugins[1]  # envelope handler is distinct

    def test_unknown_provider_falls_back_to_default(self) -> None:
        plugins = resolve_fallback_parser_plugins(
            provider_name="unknown-provider",
            model_name="some-model",
            fallback_parser_policy=DEFAULT_FALLBACK_PARSER_POLICY,
        )
        assert len(plugins) == 5


class TestRequestCompatFallbackBehavior:
    def test_default_dialect_default_identity_returns_default_profile(
        self,
    ) -> None:
        profile = resolve_openai_request_compat(
            provider_identity=None,
            request_dialect=DEFAULT_REQUEST_DIALECT,
        )
        assert profile.profile_id == "openai_default"
        assert profile.collapse_system_messages is False
        assert profile.disable_fallback_instruction is False
        assert profile.enable_structured_tool_envelope_parse is False
        assert profile.retry_empty_payload_once is False

    def test_minimax_dialect_returns_minimax_compat_profile(self) -> None:
        profile = resolve_openai_request_compat(
            provider_identity=None,
            request_dialect=MINIMAX_OPENAI_COMPAT_REQUEST_DIALECT,
        )
        assert profile.profile_id == "minimax_openai_compat"
        assert profile.collapse_system_messages is True
        assert profile.disable_fallback_instruction is True
        assert profile.enable_structured_tool_envelope_parse is True
        assert profile.retry_empty_payload_once is True
        assert "Native tool-calling contract" in profile.native_tool_only_instruction
        assert "Retry contract" in profile.empty_payload_retry_instruction

    def test_minimax_identity_fallback_path_matches_dialect_path(self) -> None:
        identity = {
            "transport_adapter": OPENAI_CHAT_TRANSPORT_ADAPTER,
            "wire_protocol_family": OPENAI_CHAT_COMPLETIONS_WIRE_PROTOCOL_FAMILY,
            "service_vendor": MINIMAX_SERVICE_VENDOR,
            "model_family": MINIMAX_MODEL_FAMILY,
        }
        identity_path = resolve_openai_request_compat(
            provider_identity=identity,
            request_dialect=DEFAULT_REQUEST_DIALECT,
        )
        dialect_path = resolve_openai_request_compat(
            provider_identity=None,
            request_dialect=MINIMAX_OPENAI_COMPAT_REQUEST_DIALECT,
        )
        assert identity_path.profile_id == dialect_path.profile_id
        assert identity_path.profile_id == "minimax_openai_compat"


class TestProfileDrivenBehaviorForShippedLanes:
    def test_openai_gpt4_profile(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="gpt-4",
            base_url="",
        )
        identity = profile.provider_identity
        assert identity is not None
        assert identity.service_vendor == OPENAI_SERVICE_VENDOR
        assert identity.model_family == GPT_MODEL_FAMILY
        assert profile.request_dialect == DEFAULT_REQUEST_DIALECT

    def test_openai_minimax_via_minimax_endpoint_profile(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="minimax-m2-7",
            base_url="https://api.minimax.io/v1",
        )
        identity = profile.provider_identity
        assert identity is not None
        assert identity.service_vendor == MINIMAX_SERVICE_VENDOR
        assert identity.model_family == MINIMAX_MODEL_FAMILY
        assert profile.request_dialect == MINIMAX_OPENAI_COMPAT_REQUEST_DIALECT

    def test_openai_minimax_via_dashscope_endpoint_profile(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="minimax-m2-7",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        identity = profile.provider_identity
        assert identity is not None
        assert identity.service_vendor == DASHSCOPE_SERVICE_VENDOR
        assert identity.model_family == MINIMAX_MODEL_FAMILY
        # Dashscope+minimax also uses the minimax_openai_compat dialect.
        assert profile.request_dialect == MINIMAX_OPENAI_COMPAT_REQUEST_DIALECT

    def test_openai_claude_profile_does_not_use_minimax_compat(self) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="claude-sonnet-4",
            base_url="",
        )
        identity = profile.provider_identity
        assert identity is not None
        assert identity.model_family == CLAUDE_MODEL_FAMILY
        assert profile.request_dialect == DEFAULT_REQUEST_DIALECT
