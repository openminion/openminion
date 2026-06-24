import os
import unittest
from tests._csc_fixtures import _csc_install_default_agent


from openminion.base.config import OpenMinionConfig
from openminion.modules.llm.providers.base import ProviderError
from openminion.modules.llm.providers.factory import build_provider, SUPPORTED_PROVIDERS
from openminion.modules.llm.providers.bridge import LLMCTLBridgeProvider


class ProviderFactoryTests(unittest.TestCase):
    def test_defaults_to_echo_provider(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        provider = build_provider(config, logger=_logger())
        self.assertIsInstance(provider, LLMCTLBridgeProvider)
        self.assertEqual(provider.name, "echo")

    def test_openai_provider_from_env_key(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="openai")
        config.providers.openai.api_key = ""
        config.providers.openai.api_key_env = "TEST_OPENAI_KEY"

        previous = os.environ.get("TEST_OPENAI_KEY")
        os.environ["TEST_OPENAI_KEY"] = "test-key"
        try:
            provider = build_provider(config, logger=_logger())
            self.assertIsInstance(provider, LLMCTLBridgeProvider)
            self.assertEqual(provider.name, "openai")
        finally:
            if previous is None:
                del os.environ["TEST_OPENAI_KEY"]
            else:
                os.environ["TEST_OPENAI_KEY"] = previous

    def test_openai_provider_prefers_stored_config_key_over_stale_env_key(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="openai")
        config.providers.openai.api_key = "stored-key"
        config.providers.openai.api_key_env = "TEST_OPENAI_KEY_OVERRIDE"

        previous = os.environ.get("TEST_OPENAI_KEY_OVERRIDE")
        os.environ["TEST_OPENAI_KEY_OVERRIDE"] = "env-key"
        try:
            provider = build_provider(config, logger=_logger())
            self.assertIsInstance(provider, LLMCTLBridgeProvider)
            self.assertEqual(
                provider._provider_config.get("api_key"),
                "stored-key",
            )
        finally:
            if previous is None:
                del os.environ["TEST_OPENAI_KEY_OVERRIDE"]
            else:
                os.environ["TEST_OPENAI_KEY_OVERRIDE"] = previous

    def test_openai_provider_prefers_process_env_over_runtime_env(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="openai")
        config.providers.openai.api_key = ""
        config.providers.openai.api_key_env = "TEST_OPENAI_KEY_OVERRIDE"
        config.runtime.env = {"TEST_OPENAI_KEY_OVERRIDE": "runtime-key"}

        previous = os.environ.get("TEST_OPENAI_KEY_OVERRIDE")
        os.environ["TEST_OPENAI_KEY_OVERRIDE"] = "process-key"
        try:
            provider = build_provider(config, logger=_logger())
            self.assertIsInstance(provider, LLMCTLBridgeProvider)
            self.assertEqual(
                provider._provider_config.get("api_key"),
                "process-key",
            )
        finally:
            if previous is None:
                del os.environ["TEST_OPENAI_KEY_OVERRIDE"]
            else:
                os.environ["TEST_OPENAI_KEY_OVERRIDE"] = previous

    def test_openai_provider_requires_key(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="openai")
        config.providers.openai.api_key = ""
        config.providers.openai.api_key_env = "TEST_OPENAI_KEY_MISSING"

        previous = os.environ.pop("TEST_OPENAI_KEY_MISSING", None)
        try:
            with self.assertRaises(ProviderError):
                build_provider(config, logger=_logger())
        finally:
            if previous is not None:
                os.environ["TEST_OPENAI_KEY_MISSING"] = previous

    def test_openai_provider_translates_legacy_minimax_identity_for_bridge(
        self,
    ) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="openai")
        config.providers.openai.api_key = "test-key"
        config.providers.openai.model = "MiniMax-M2.7"
        config.providers.openai.base_url = "https://api.minimax.io/v1"

        provider = build_provider(config, logger=_logger())

        self.assertIsInstance(provider, LLMCTLBridgeProvider)
        self.assertEqual(
            provider._provider_config.get("provider_identity"),
            {
                "transport_adapter": "openai_chat",
                "wire_protocol_family": "openai_chat_completions",
                "service_vendor": "minimax",
                "model_family": "minimax",
            },
        )

    def test_anthropic_provider_from_env_key(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="anthropic")
        config.providers.anthropic.api_key = ""
        config.providers.anthropic.api_key_env = "TEST_ANTHROPIC_KEY"

        previous = os.environ.get("TEST_ANTHROPIC_KEY")
        os.environ["TEST_ANTHROPIC_KEY"] = "test-key"
        try:
            provider = build_provider(config, logger=_logger())
            self.assertIsInstance(provider, LLMCTLBridgeProvider)
            self.assertEqual(provider.name, "anthropic")
        finally:
            if previous is None:
                del os.environ["TEST_ANTHROPIC_KEY"]
            else:
                os.environ["TEST_ANTHROPIC_KEY"] = previous

    def test_claude_alias_uses_anthropic_bridge(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="claude")
        config.providers.anthropic.api_key = "test-key"

        provider = build_provider(config, logger=_logger())
        self.assertIsInstance(provider, LLMCTLBridgeProvider)
        self.assertEqual(provider.name, "claude")

    def test_openrouter_provider_from_env_key(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="openrouter")
        config.providers.openrouter.api_key = ""
        config.providers.openrouter.api_key_env = "TEST_OPENROUTER_KEY"

        previous = os.environ.get("TEST_OPENROUTER_KEY")
        os.environ["TEST_OPENROUTER_KEY"] = "test-key"
        try:
            provider = build_provider(config, logger=_logger())
            self.assertIsInstance(provider, LLMCTLBridgeProvider)
            self.assertEqual(provider.name, "openrouter")
        finally:
            if previous is None:
                del os.environ["TEST_OPENROUTER_KEY"]
            else:
                os.environ["TEST_OPENROUTER_KEY"] = previous

    def test_cerebras_provider_from_env_key(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="cerebras")
        config.providers.cerebras.api_key = ""
        config.providers.cerebras.api_key_env = "TEST_CEREBRAS_KEY"

        previous = os.environ.get("TEST_CEREBRAS_KEY")
        os.environ["TEST_CEREBRAS_KEY"] = "test-key"
        try:
            provider = build_provider(config, logger=_logger())
            self.assertIsInstance(provider, LLMCTLBridgeProvider)
            self.assertEqual(provider.name, "cerebras")
        finally:
            if previous is None:
                del os.environ["TEST_CEREBRAS_KEY"]
            else:
                os.environ["TEST_CEREBRAS_KEY"] = previous

    def test_groq_provider_from_env_key(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="groq")
        config.providers.groq.api_key = ""
        config.providers.groq.api_key_env = "TEST_GROQ_KEY"

        previous = os.environ.get("TEST_GROQ_KEY")
        os.environ["TEST_GROQ_KEY"] = "test-key"
        try:
            provider = build_provider(config, logger=_logger())
            self.assertIsInstance(provider, LLMCTLBridgeProvider)
            self.assertEqual(provider.name, "groq")
        finally:
            if previous is None:
                del os.environ["TEST_GROQ_KEY"]
            else:
                os.environ["TEST_GROQ_KEY"] = previous

    def test_ollama_provider_does_not_require_key(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="ollama")
        config.providers.ollama.api_key = ""
        config.providers.ollama.api_key_env = "TEST_OLLAMA_KEY_MISSING"
        previous = os.environ.pop("TEST_OLLAMA_KEY_MISSING", None)
        try:
            provider = build_provider(config, logger=_logger())
            self.assertIsInstance(provider, LLMCTLBridgeProvider)
            self.assertEqual(provider.name, "ollama")
        finally:
            if previous is not None:
                os.environ["TEST_OLLAMA_KEY_MISSING"] = previous

    def test_openrouter_provider_requires_key(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="openrouter")
        config.providers.openrouter.api_key = ""
        config.providers.openrouter.api_key_env = "TEST_OPENROUTER_KEY_MISSING"

        previous = os.environ.pop("TEST_OPENROUTER_KEY_MISSING", None)
        try:
            with self.assertRaises(ProviderError):
                build_provider(config, logger=_logger())
        finally:
            if previous is not None:
                os.environ["TEST_OPENROUTER_KEY_MISSING"] = previous

    def test_cerebras_provider_requires_key(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="cerebras")
        config.providers.cerebras.api_key = ""
        config.providers.cerebras.api_key_env = "TEST_CEREBRAS_KEY_MISSING"

        previous = os.environ.pop("TEST_CEREBRAS_KEY_MISSING", None)
        try:
            with self.assertRaises(ProviderError):
                build_provider(config, logger=_logger())
        finally:
            if previous is not None:
                os.environ["TEST_CEREBRAS_KEY_MISSING"] = previous

    def test_groq_provider_requires_key(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="groq")
        config.providers.groq.api_key = ""
        config.providers.groq.api_key_env = "TEST_GROQ_KEY_MISSING"

        previous = os.environ.pop("TEST_GROQ_KEY_MISSING", None)
        try:
            with self.assertRaises(ProviderError):
                build_provider(config, logger=_logger())
        finally:
            if previous is not None:
                os.environ["TEST_GROQ_KEY_MISSING"] = previous

    def test_cortensor_provider_does_not_require_key(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="cortensor")
        config.providers.cortensor.api_key = ""
        config.providers.cortensor.api_key_env = "TEST_CORTENSOR_KEY_MISSING"
        previous = os.environ.pop("TEST_CORTENSOR_KEY_MISSING", None)
        try:
            provider = build_provider(config, logger=_logger())
            self.assertIsInstance(provider, LLMCTLBridgeProvider)
            self.assertEqual(provider.name, "cortensor")
        finally:
            if previous is not None:
                os.environ["TEST_CORTENSOR_KEY_MISSING"] = previous

    def test_cortensor_runtime_overrides_from_env(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="cortensor")
        config.providers.cortensor.base_url = "http://127.0.0.1:8080/api/v2/completions"
        config.providers.cortensor.api_mode = "auto"
        config.providers.cortensor.session_id = 1
        config.providers.cortensor.session_ids = [45]
        config.providers.cortensor.session_pool = "auto"
        config.providers.cortensor.dedicated_session_ids = [47]
        config.providers.cortensor.ephemeral_session_ids = [44]
        config.providers.cortensor.session_parallel_requests = 1
        config.providers.cortensor.session_retry_rounds = 1
        previous_url = os.environ.get("CORTENSOR_API_URL")
        previous_mode = os.environ.get("CORTENSOR_API_MODE")
        previous_session = os.environ.get("CORTENSOR_SESSION_ID")
        previous_sessions = os.environ.get("CORTENSOR_SESSION_IDS")
        previous_session_pool = os.environ.get("CORTENSOR_SESSION_POOL")
        previous_dedicated_sessions = os.environ.get("CORTENSOR_DEDICATED_SESSION_IDS")
        previous_ephemeral_sessions = os.environ.get("CORTENSOR_EPHEMERAL_SESSION_IDS")
        previous_parallel = os.environ.get("CORTENSOR_SESSION_PARALLEL_REQUESTS")
        previous_retry_rounds = os.environ.get("CORTENSOR_SESSION_RETRY_ROUNDS")
        previous_max_tokens = os.environ.get("CORTENSOR_MAX_TOKENS")
        os.environ["CORTENSOR_API_URL"] = "https://router.example/api/v2/completions"
        os.environ["CORTENSOR_API_MODE"] = "cortensor_completion"
        os.environ["CORTENSOR_SESSION_ID"] = "29"
        os.environ["CORTENSOR_SESSION_IDS"] = "44,45"
        os.environ["CORTENSOR_SESSION_POOL"] = "dedicated"
        os.environ["CORTENSOR_DEDICATED_SESSION_IDS"] = "47,48"
        os.environ["CORTENSOR_EPHEMERAL_SESSION_IDS"] = "49,50"
        os.environ["CORTENSOR_SESSION_PARALLEL_REQUESTS"] = "2"
        os.environ["CORTENSOR_SESSION_RETRY_ROUNDS"] = "3"
        os.environ["CORTENSOR_MAX_TOKENS"] = "4096"
        try:
            provider = build_provider(config, logger=_logger())
            self.assertIsInstance(provider, LLMCTLBridgeProvider)
            self.assertEqual(provider.name, "cortensor")
            # Verify env vars propagated into provider_config
            self.assertEqual(
                provider._provider_config["base_url"],
                "https://router.example/api/v2/completions",
            )
            self.assertEqual(
                provider._provider_config["api_mode"], "cortensor_completion"
            )
            self.assertEqual(provider._provider_config["session_id"], 29)
            self.assertEqual(provider._provider_config["session_ids"], [44, 45])
            self.assertEqual(provider._provider_config["session_pool"], "dedicated")
            self.assertEqual(
                provider._provider_config["dedicated_session_ids"], [47, 48]
            )
            self.assertEqual(
                provider._provider_config["ephemeral_session_ids"], [49, 50]
            )
            self.assertEqual(provider._provider_config["session_parallel_requests"], 2)
            self.assertEqual(provider._provider_config["session_retry_rounds"], 3)
            self.assertEqual(provider._provider_config["max_tokens"], 4096)
        finally:
            if previous_url is None:
                os.environ.pop("CORTENSOR_API_URL", None)
            else:
                os.environ["CORTENSOR_API_URL"] = previous_url
            if previous_mode is None:
                os.environ.pop("CORTENSOR_API_MODE", None)
            else:
                os.environ["CORTENSOR_API_MODE"] = previous_mode
            if previous_session is None:
                os.environ.pop("CORTENSOR_SESSION_ID", None)
            else:
                os.environ["CORTENSOR_SESSION_ID"] = previous_session
            if previous_sessions is None:
                os.environ.pop("CORTENSOR_SESSION_IDS", None)
            else:
                os.environ["CORTENSOR_SESSION_IDS"] = previous_sessions
            if previous_session_pool is None:
                os.environ.pop("CORTENSOR_SESSION_POOL", None)
            else:
                os.environ["CORTENSOR_SESSION_POOL"] = previous_session_pool
            if previous_dedicated_sessions is None:
                os.environ.pop("CORTENSOR_DEDICATED_SESSION_IDS", None)
            else:
                os.environ["CORTENSOR_DEDICATED_SESSION_IDS"] = (
                    previous_dedicated_sessions
                )
            if previous_ephemeral_sessions is None:
                os.environ.pop("CORTENSOR_EPHEMERAL_SESSION_IDS", None)
            else:
                os.environ["CORTENSOR_EPHEMERAL_SESSION_IDS"] = (
                    previous_ephemeral_sessions
                )
            if previous_parallel is None:
                os.environ.pop("CORTENSOR_SESSION_PARALLEL_REQUESTS", None)
            else:
                os.environ["CORTENSOR_SESSION_PARALLEL_REQUESTS"] = previous_parallel
            if previous_retry_rounds is None:
                os.environ.pop("CORTENSOR_SESSION_RETRY_ROUNDS", None)
            else:
                os.environ["CORTENSOR_SESSION_RETRY_ROUNDS"] = previous_retry_rounds
            if previous_max_tokens is None:
                os.environ.pop("CORTENSOR_MAX_TOKENS", None)
            else:
                os.environ["CORTENSOR_MAX_TOKENS"] = previous_max_tokens

    def test_cortensor_bridge_preserves_provider_timing_fields(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="cortensor")
        config.providers.cortensor.timeout_seconds = 150
        config.providers.cortensor.transport_timeout_buffer_seconds = 25
        config.providers.cortensor.precommit_timeout_seconds = 180
        config.providers.cortensor.result_wait_attempts = 5
        config.providers.cortensor.result_wait_interval_seconds = 1.25
        config.providers.cortensor.session_parallel_requests = 3
        config.providers.cortensor.session_retry_rounds = 4
        config.providers.cortensor.session_pool = "mixed"
        config.providers.cortensor.session_ids = [45, 46]
        config.providers.cortensor.dedicated_session_ids = [47]
        config.providers.cortensor.ephemeral_session_ids = [44]

        provider = build_provider(config, logger=_logger())
        self.assertIsInstance(provider, LLMCTLBridgeProvider)

        self.assertEqual(provider._provider_config["timeout_seconds"], 150)
        self.assertEqual(
            provider._provider_config["transport_timeout_buffer_seconds"], 25
        )
        self.assertEqual(provider._provider_config["precommit_timeout_seconds"], 180)
        self.assertEqual(provider._provider_config["result_wait_attempts"], 5)
        self.assertEqual(
            provider._provider_config["result_wait_interval_seconds"], 1.25
        )
        self.assertEqual(provider._provider_config["session_parallel_requests"], 3)
        self.assertEqual(provider._provider_config["session_retry_rounds"], 4)
        self.assertEqual(provider._provider_config["session_pool"], "mixed")
        self.assertEqual(provider._provider_config["session_ids"], [45, 46])
        self.assertEqual(provider._provider_config["dedicated_session_ids"], [47])
        self.assertEqual(provider._provider_config["ephemeral_session_ids"], [44])

        # match cortensor provider timeout floor behavior for bridge-level request timeout
        self.assertEqual(
            provider._runtime.config.llmctl.timeouts.request_timeout_sec, 265
        )
        # avoid layering extra llmctl retries on top of cortensor's own session retry strategy
        self.assertEqual(provider._runtime.config.llmctl.retries.max_retries, 0)

    def test_bridge_unavailable_raises_error(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="echo")

        previous_disable_bridge = os.environ.get("OPENMINION_DISABLE_LLMCTL_BRIDGE")
        os.environ["OPENMINION_DISABLE_LLMCTL_BRIDGE"] = "1"
        try:
            with self.assertRaises(ProviderError) as context:
                build_provider(config, logger=_logger())
        finally:
            if previous_disable_bridge is None:
                os.environ.pop("OPENMINION_DISABLE_LLMCTL_BRIDGE", None)
            else:
                os.environ["OPENMINION_DISABLE_LLMCTL_BRIDGE"] = previous_disable_bridge

        self.assertIn("openminion.modules.llm", str(context.exception).lower())

    def test_unknown_provider_raises_error(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, provider="nonexistent_provider")

        with self.assertRaises(ProviderError) as context:
            build_provider(config, logger=_logger())

        self.assertIn("nonexistent_provider", str(context.exception))

    def test_supported_providers_is_complete(self) -> None:
        expected = {
            "echo",
            "openai",
            "anthropic",
            "claude",
            "openrouter",
            "cerebras",
            "groq",
            "ollama",
            "cortensor",
        }
        self.assertEqual(SUPPORTED_PROVIDERS, expected)


def _logger():
    import logging

    return logging.getLogger("openminion.tests.provider-factory")
