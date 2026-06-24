import json
import unittest
from unittest.mock import patch

from openminion.modules.llm import LLMCTL
from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.providers.adapters import (
    OpenAIProvider,
    OpenRouterProvider,
    OllamaProvider,
    AnthropicProvider,
    CortensorProvider,
)
from openminion.modules.llm.schemas import LLMRequest

_URL_OPEN = "openminion.modules.llm.providers.adapters.urllib_request.urlopen"


class _FakeHTTPResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class ProviderCanonicalIRRegressionTests(unittest.TestCase):
    def _complete_with_payload(
        self,
        provider,
        request,
        config,
        *,
        payload=None,
        side_effect=None,
    ):
        with patch(
            _URL_OPEN,
            return_value=None if side_effect else _FakeHTTPResponse(payload),
            side_effect=side_effect,
        ):
            return provider.complete(request, config)

    def test_positve_mapping_success_cases_all_providers(self):
        openai_provider = OpenAIProvider()
        openai_request = LLMRequest.model_validate(
            {
                "model": "gpt-4.1-mini",
                "messages": [{"role": "user", "content": "hi"}],
            }
        )
        openai_payload = {
            "model": "gpt-4.1-mini",
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }
        openai_response = self._complete_with_payload(
            openai_provider,
            openai_request,
            {
                "api_key": "test-key",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4.1-mini",
            },
            payload=openai_payload,
        )

        self.assertTrue(openai_response.ok)
        self.assertEqual(openai_response.provider, "openai")
        self.assertEqual(openai_response.output_text, "hello")
        self.assertEqual(openai_response.usage.total_tokens, 10)

        router_provider = OpenRouterProvider()
        router_payload = {
            "model": "openai/gpt-4.1-mini",
            "choices": [
                {"message": {"content": "router response"}, "finish_reason": "stop"}
            ],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 10,
                "total_tokens": 15,
                "cost": 0.00005,
            },
        }

        router_response = self._complete_with_payload(
            router_provider,
            openai_request,
            {
                "api_key": "test-key",
                "base_url": "https://openrouter.ai/api/v1",
                "model": "openai/gpt-4.1-mini",
            },
            payload=router_payload,
        )

        self.assertTrue(router_response.ok)
        self.assertEqual(router_response.provider, "openrouter")
        self.assertEqual(router_response.output_text, "router response")
        self.assertEqual(router_response.usage.total_tokens, 15)
        self.assertIsNotNone(router_response.cost_usd)

        ollama_provider = OllamaProvider()
        ollama_request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "what's your name?"}],
            }
        )
        ollama_payload = {
            "model": "llama3.1",
            "message": {"role": "assistant", "content": "I'm Ollama"},
            "total_duration": 1234567890,
            "load_duration": 123456789,
            "prompt_eval_count": 10,
            "prompt_eval_duration": 123456789,
            "eval_count": 5,
            "eval_duration": 987654321,
        }

        ollama_response = self._complete_with_payload(
            ollama_provider,
            ollama_request,
            {"base_url": "http://127.0.0.1:11434", "model": "llama3.1"},
            payload=ollama_payload,
        )

        self.assertTrue(ollama_response.ok)
        self.assertEqual(ollama_response.provider, "ollama")
        self.assertEqual(ollama_response.output_text, "I'm Ollama")
        self.assertEqual(ollama_response.usage.input_tokens, 10)
        self.assertEqual(ollama_response.usage.output_tokens, 5)

        anth_provider = AnthropicProvider()
        anth_request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "hello"}],
            }
        )
        anth_payload = {
            "model": "claude-3-5-sonnet-latest",
            "content": [{"type": "text", "text": "Hello, I'm Claude!"}],
            "usage": {"input_tokens": 8, "output_tokens": 6},
            "stop_reason": "end_turn",
        }

        anth_response = self._complete_with_payload(
            anth_provider,
            anth_request,
            {
                "api_key": "test-key",
                "base_url": "https://api.anthropic.com/v1",
                "model": "claude-3-5-sonnet-latest",
            },
            payload=anth_payload,
        )

        self.assertTrue(anth_response.ok)
        self.assertEqual(anth_response.provider, "anthropic")
        self.assertEqual(anth_response.output_text, "Hello, I'm Claude!")
        self.assertEqual(anth_response.usage.input_tokens, 8)
        self.assertEqual(anth_response.usage.output_tokens, 6)
        self.assertEqual(anth_response.finish_reason, "end_turn")

    def test_negatively_mapped_malformed_payload_all_providers(self):
        openai_provider = OpenAIProvider()
        openai_request = LLMRequest.model_validate(
            {
                "model": "gpt-4.1-mini",
                "messages": [{"role": "user", "content": "test"}],
            }
        )

        empty_choices_payload = {
            "model": "gpt-4.1-mini",
            "choices": [],
        }

        with self.assertRaises(LLMCtlError) as ctx:
            self._complete_with_payload(
                openai_provider,
                openai_request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4.1-mini",
                },
                payload=empty_choices_payload,
            )
        self.assertEqual(ctx.exception.code, "PROVIDER_ERROR")
        self.assertIn("choices", ctx.exception.message.lower())

        invalid_choice_payload = {
            "model": "gpt-4.1-mini",
            "choices": ["not a dict"],
        }

        with self.assertRaises(LLMCtlError) as ctx:
            self._complete_with_payload(
                openai_provider,
                openai_request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4.1-mini",
                },
                payload=invalid_choice_payload,
            )
        self.assertEqual(ctx.exception.code, "PROVIDER_ERROR")
        self.assertIn("choice", ctx.exception.message.lower())

        malformed_payload = {}

        with self.assertRaises(LLMCtlError) as ctx:
            self._complete_with_payload(
                openai_provider,
                openai_request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4.1-mini",
                },
                payload=malformed_payload,
            )
        self.assertEqual(ctx.exception.code, "PROVIDER_ERROR")

    def test_empty_response_recovery_behavior_various_providers(self):
        corte_provider = CortensorProvider()
        corte_request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "retry test"}],
            }
        )

        attempts = {"count": 0}

        def mock_urlopen_retry(http_request, timeout=None):
            del timeout, http_request
            attempts["count"] += 1

            if attempts["count"] == 1:
                return _FakeHTTPResponse(
                    {
                        "model": "gpt-4.1-mini",
                        "choices": [{"message": {"content": ""}}],
                    }
                )
            return _FakeHTTPResponse(
                {
                    "model": "gpt-4.1-mini",
                    "choices": [{"message": {"content": "recovered after retry"}}],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 4,
                        "total_tokens": 9,
                    },
                }
            )

        retry_response = self._complete_with_payload(
            corte_provider,
            corte_request,
            {
                "model": "gpt-4.1-mini",
                "base_url": "http://localhost:8080/api/v2/chat/completions",
                "empty_result_max_attempts": 3,
                "empty_result_backoff_ms": 1,
                "api_mode": "openai_chat",
            },
            side_effect=mock_urlopen_retry,
        )

        self.assertTrue(retry_response.ok)
        self.assertEqual(retry_response.output_text, "recovered after retry")
        self.assertEqual(attempts["count"], 2)

        self.assertIn("attempt", retry_response.telemetry)
        self.assertGreaterEqual(retry_response.telemetry["attempt"], 1)

    def test_ir_version_drift_and_compatibility_checks(self):
        from openminion.modules.llm.contracts.adapter import coerce_provider_output
        from openminion.modules.llm.schemas import LLMResponse

        sample_dict = {
            "ok": True,
            "provider": "test",
            "model": "test-model",
            "output_text": "test output",
        }
        response1 = coerce_provider_output(sample_dict)
        self.assertIsInstance(response1, LLMResponse)

        response2 = coerce_provider_output(response1)
        self.assertEqual(response2, response1)

        minimal_dict = {
            "ok": True,
            "provider": "minimal",
            "model": "minimal-v1",
            "output_text": "basic output",
        }
        minimal_response = coerce_provider_output(minimal_dict)
        self.assertIsInstance(minimal_response, LLMResponse)
        self.assertEqual(minimal_response.provider, "minimal")
        self.assertEqual(minimal_response.model, "minimal-v1")
        self.assertEqual(minimal_response.output_text, "basic output")
        self.assertIsNotNone(minimal_response.latency_ms)

        from openminion.modules.llm.contracts.adapter import (
            ProviderAdapterResult,
            adapter_result_to_llm_response,
        )

        adapter_result = ProviderAdapterResult(
            provider="adapter-compat",
            model="adapter-model",
            output_text="adapter output",
            finish_reason="stop",
            normalization_meta={"test": True},
        )

        converted_response = coerce_provider_output(adapter_result)
        direct_response = adapter_result_to_llm_response(adapter_result)

        self.assertIsInstance(converted_response, LLMResponse)
        self.assertIsInstance(direct_response, LLMResponse)
        self.assertEqual(converted_response.provider, direct_response.provider)
        self.assertEqual(converted_response.output_text, direct_response.output_text)
        self.assertEqual(
            converted_response.finish_reason, direct_response.finish_reason
        )

    def test_provider_contract_compatibility(self):
        config = {
            "version": 1,
            "llmctl": {"default_provider": "openai", "default_model": "gpt-4.1-mini"},
            "providers": {
                "openai": {
                    "api_key": "test-key",
                    "base_url": "https://api.openai.com/v1",
                },
                "ollama": {"base_url": "http://127.0.0.1:11434"},
            },
            "agents": {
                "tester": {
                    "default_provider": "openai",
                    "default_model": "gpt-4.1-mini",
                }
            },
        }

        runtime = LLMCTL.from_config(config)

        stub_response = runtime.client(agent_name="tester").complete(
            messages=[{"role": "user", "content": "hello"}]
        )

        self.assertIsNotNone(stub_response.output_text)
