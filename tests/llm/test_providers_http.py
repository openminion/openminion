import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from unittest.mock import patch

from openminion.modules.llm import LLMCTL
from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.providers.adapters import (
    AnthropicProvider,
    CortensorProvider,
    EchoProvider,
    OllamaProvider,
    OpenAIProvider,
    OpenRouterProvider,
)
from openminion.modules.llm.schemas import ImageContentPart, LLMRequest, TextContentPart

_FIXTURES_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "provider_tool_call_source_precedence"
)


def _load_fixture_payload(filename: str) -> dict[str, Any]:
    return json.loads((_FIXTURES_ROOT / filename).read_text(encoding="utf-8"))


class _FakeHTTPResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class ProviderHTTPTests(unittest.TestCase):
    def test_registry_includes_all_openminion_provider_names(self) -> None:
        cfg = {
            "version": 1,
            "llmctl": {"default_provider": "stub", "default_model": "stub-v1"},
            "providers": {},
            "agents": {
                "default": {"default_provider": "stub", "default_model": "stub-v1"}
            },
        }
        runtime = LLMCTL.from_config(cfg)
        names = set(runtime.registry.list().keys())
        expected = {
            "stub",
            "local",
            "echo",
            "openai",
            "openrouter",
            "anthropic",
            "claude",
            "ollama",
            "groq",
            "cerebras",
            "cortensor",
        }
        self.assertTrue(expected.issubset(names))

    def test_echo_provider(self) -> None:
        provider = EchoProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "hi"}],
                "metadata": {"thinking": "minimal"},
            }
        )
        response = provider.complete(request, {})
        self.assertTrue(response.ok)
        self.assertIn("hi", response.output_text)

    def test_openai_parses_native_tool_calls(self) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "gpt-4.1-mini",
                "messages": [{"role": "user", "content": "weather"}],
                "tools": [
                    {"name": "weather", "description": "Lookup", "input_schema": {}}
                ],
                "tool_choice": "required",
            }
        )
        payload = {
            "model": "gpt-4.1-mini",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "weather",
                                    "arguments": '{"location":"NYC"}',
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=_FakeHTTPResponse(payload),
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.openai.com/v1",
                    "tool_call_strategy": "native",
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.model, "gpt-4.1-mini")
        self.assertEqual(response.usage.total_tokens, 18)
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].name, "weather")
        self.assertEqual(response.tool_calls[0].arguments.get("location"), "NYC")
        self.assertEqual(
            response.telemetry.get("normalization", {}).get("tool_call_source"),
            "native",
        )

    def test_openai_preserves_reasoning_as_thinking_blocks(self) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "o3",
                "messages": [{"role": "user", "content": "think, then call weather"}],
            }
        )
        payload = {
            "model": "o3",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "Done",
                        "reasoning": {
                            "text": "I should inspect the weather tool first."
                        },
                    },
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        }

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=_FakeHTTPResponse(payload),
        ):
            response = provider.complete(
                request,
                {"api_key": "test-key", "base_url": "https://api.openai.com/v1"},
            )

        assert len(response.thinking) == 1
        assert (
            response.thinking[0]["content"]
            == "I should inspect the weather tool first."
        )

    def test_openai_preserves_inline_think_blocks_from_openai_compatible_content(
        self,
    ) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "MiniMax-M2.7",
                "messages": [{"role": "user", "content": "research with tools"}],
                "tools": [
                    {
                        "name": "web.search",
                        "description": "Search the web",
                        "input_schema": {},
                    }
                ],
                "tool_choice": "auto",
            }
        )
        payload = _load_fixture_payload(
            "openai_compatible_minimax_m2_7_step01_call01.json"
        )

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=_FakeHTTPResponse(payload),
        ):
            response = provider.complete(
                request,
                {"api_key": "test-key", "base_url": "https://api.openai.com/v1"},
            )

        assert len(response.thinking) == 1
        assert (
            response.thinking[0]["content"]
            == "The user wants me to research whether `uv` or `pipx` is better for "
            "installing Python CLI apps on macOS. Let me gather current information "
            "about both tools to make a fair comparison.\n\nI'll search for current "
            "information on both tools."
        )
        assert response.output_text == ""
        assert len(response.tool_calls) == 1

    def test_openrouter_preserves_reasoning_as_thinking_blocks(self) -> None:
        provider = OpenRouterProvider()
        request = LLMRequest.model_validate(
            {
                "model": "deepseek/deepseek-r1",
                "messages": [{"role": "user", "content": "reason before answering"}],
            }
        )
        payload = {
            "model": "deepseek/deepseek-r1",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "Done",
                        "reasoning": "Need to inspect the tool result before replying.",
                    },
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        }

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=_FakeHTTPResponse(payload),
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://openrouter.ai/api/v1",
                },
            )

        assert len(response.thinking) == 1
        assert (
            response.thinking[0]["content"]
            == "Need to inspect the tool result before replying."
        )

    def test_openai_preserves_native_tool_calls_when_reasoning_text_present(
        self,
    ) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "gpt-4.1-mini",
                "messages": [{"role": "user", "content": "weather"}],
                "tools": [
                    {"name": "weather", "description": "Lookup", "input_schema": {}}
                ],
                "tool_choice": "auto",
            }
        )
        payload = {
            "model": "gpt-4.1-mini",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "reasoning": "I should call the tool rather than answer in prose.",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "weather",
                                    "arguments": '{"location":"NYC"}',
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=_FakeHTTPResponse(payload),
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.openai.com/v1",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(len(response.tool_calls), 1)
        normalization = response.telemetry.get("normalization", {})
        self.assertEqual(normalization.get("tool_call_source"), "native")
        self.assertEqual(
            normalization.get("tool_call_skipped_fallback_sources"),
            ["message.reasoning"],
        )

    def test_openai_dashscope_minimax_preserves_submit_output_target_without_fallback_prompt(
        self,
    ) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "MiniMax-M2.5",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [
                    {
                        "name": "submit_output",
                        "description": "return structured output",
                        "input_schema": {"type": "object"},
                    }
                ],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "submit_output"},
                },
            }
        )
        payload = {
            "model": "MiniMax-M2.5",
            "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        captured: dict[str, object] = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://coding-intl.dashscope.aliyuncs.com/v1",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        body = captured.get("body")
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        self.assertEqual(
            body.get("tool_choice"),
            {"type": "function", "function": {"name": "submit_output"}},
        )
        messages = body.get("messages")
        self.assertIsInstance(messages, list)
        assert isinstance(messages, list)
        rendered = "\n".join(
            str(item.get("content", "") or "")
            for item in messages
            if isinstance(item, dict)
        )
        self.assertNotIn("Tool-calling contract:", rendered)
        self.assertIn("Native tool-calling contract:", rendered)

    def test_openai_official_minimax_preserves_submit_output_target_and_collapses_system_messages(
        self,
    ) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "MiniMax-M2.7",
                "messages": [
                    {"role": "system", "content": "Primary system context."},
                    {"role": "system", "content": "Structured tool contract."},
                    {"role": "user", "content": "hi"},
                ],
                "tools": [
                    {
                        "name": "submit_output",
                        "description": "return structured output",
                        "input_schema": {"type": "object"},
                    }
                ],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "submit_output"},
                },
            }
        )
        payload = {
            "model": "MiniMax-M2.7",
            "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        captured: dict[str, object] = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.minimax.io/v1",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(
            dict(response.telemetry or {})
            .get("normalization", {})
            .get("request_compat_profile"),
            "minimax_openai_compat",
        )
        body = captured.get("body")
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        self.assertEqual(
            body.get("tool_choice"),
            {"type": "function", "function": {"name": "submit_output"}},
        )
        messages = body.get("messages")
        self.assertIsInstance(messages, list)
        assert isinstance(messages, list)
        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        rendered = "\n".join(
            str(item.get("content", "") or "")
            for item in messages
            if isinstance(item, dict)
        )
        self.assertNotIn("Tool-calling contract:", rendered)
        self.assertIn("Native tool-calling contract:", rendered)

    def test_openai_official_minimax_collapses_system_text_content_parts_cleanly(
        self,
    ) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "MiniMax-M2.7",
                "messages": [
                    {
                        "role": "system",
                        "content": "Primary system context.",
                        "content_parts": [
                            {
                                "type": "text",
                                "text": "Primary system context.",
                                "block_kind": "static_prefix",
                                "cache_eligible": True,
                                "segment_ids": ["static_prefix"],
                            }
                        ],
                    },
                    {
                        "role": "system",
                        "content": "Structured tool contract.",
                        "content_parts": [
                            {
                                "type": "text",
                                "text": "Structured tool contract.",
                                "block_kind": "mission_snapshot",
                                "cache_eligible": False,
                                "segment_ids": ["mission_snapshot"],
                            }
                        ],
                    },
                    {"role": "user", "content": "hi"},
                ],
                "tools": [
                    {
                        "name": "submit_output",
                        "description": "return structured output",
                        "input_schema": {"type": "object"},
                    }
                ],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "submit_output"},
                },
            }
        )
        payload = {
            "model": "MiniMax-M2.7",
            "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        captured: dict[str, object] = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.minimax.io/v1",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        body = captured.get("body")
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        messages = body.get("messages")
        self.assertIsInstance(messages, list)
        assert isinstance(messages, list)
        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        system_text = str(messages[0].get("content", "") or "")
        self.assertIn("Primary system context.", system_text)
        self.assertIn("Structured tool contract.", system_text)
        self.assertNotIn("block_kind", system_text)

    def test_openai_default_hybrid_keeps_fallback_instruction_enabled(self) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "gpt-4.1-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [
                    {
                        "name": "submit_output",
                        "description": "return structured output",
                        "input_schema": {"type": "object"},
                    }
                ],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "submit_output"},
                },
            }
        )
        payload = {
            "model": "gpt-4.1-mini",
            "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        captured: dict[str, object] = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.openai.com/v1",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        body = captured.get("body")
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        messages = body.get("messages")
        self.assertIsInstance(messages, list)
        assert isinstance(messages, list)
        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        rendered = "\n".join(
            str(item.get("content", "") or "")
            for item in messages
            if isinstance(item, dict)
        )
        self.assertIn("Tool-calling contract:", rendered)

    def test_anthropic_prompt_cache_enabled_marks_system_text_block(self) -> None:
        provider = AnthropicProvider()
        request = LLMRequest.model_validate(
            {
                "model": "claude-3-5-sonnet-latest",
                "messages": [
                    {
                        "role": "system",
                        "content": "Static prefix",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"role": "user", "content": "hi"},
                ],
            }
        )
        payload = {
            "model": "claude-3-5-sonnet-latest",
            "content": [{"type": "text", "text": "hello"}],
            "usage": {"input_tokens": 3, "output_tokens": 2},
            "stop_reason": "end_turn",
        }
        captured: dict[str, object] = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.anthropic.com/v1",
                    "prompt_cache": {"enabled": True, "cache_system_prompt": True},
                },
            )

        self.assertTrue(response.ok)
        body = captured.get("body")
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        system_payload = body.get("system")
        self.assertIsInstance(system_payload, list)
        assert isinstance(system_payload, list)
        self.assertEqual(
            system_payload[0],
            {
                "type": "text",
                "text": "Static prefix",
                "cache_control": {"type": "ephemeral"},
            },
        )

    def test_anthropic_prompt_cache_enabled_accepts_system_text_content_parts(
        self,
    ) -> None:
        provider = AnthropicProvider()
        request = LLMRequest.model_validate(
            {
                "model": "claude-3-5-sonnet-latest",
                "messages": [
                    {
                        "role": "system",
                        "content": "Static prefix",
                        "cache_control": {"type": "ephemeral"},
                        "content_parts": [
                            {
                                "type": "text",
                                "text": "Static prefix",
                                "block_kind": "static_prefix",
                                "cache_eligible": True,
                                "segment_ids": ["static_prefix"],
                            }
                        ],
                    },
                    {"role": "user", "content": "hi"},
                ],
            }
        )
        payload = {
            "model": "claude-3-5-sonnet-latest",
            "content": [{"type": "text", "text": "hello"}],
            "usage": {"input_tokens": 3, "output_tokens": 2},
            "stop_reason": "end_turn",
        }
        captured: dict[str, object] = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.anthropic.com/v1",
                    "prompt_cache": {"enabled": True, "cache_system_prompt": True},
                },
            )

        self.assertTrue(response.ok)
        body = captured.get("body")
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        system_payload = body.get("system")
        self.assertIsInstance(system_payload, list)
        assert isinstance(system_payload, list)
        self.assertEqual(
            system_payload[0],
            {
                "type": "text",
                "text": "Static prefix",
                "cache_control": {"type": "ephemeral"},
            },
        )

    def test_anthropic_prompt_cache_disabled_keeps_system_prompt_flat(self) -> None:
        provider = AnthropicProvider()
        request = LLMRequest.model_validate(
            {
                "model": "claude-3-5-sonnet-latest",
                "messages": [
                    {
                        "role": "system",
                        "content": "Static prefix",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"role": "user", "content": "hi"},
                ],
            }
        )
        payload = {
            "model": "claude-3-5-sonnet-latest",
            "content": [{"type": "text", "text": "hello"}],
            "usage": {"input_tokens": 3, "output_tokens": 2},
            "stop_reason": "end_turn",
        }
        captured: dict[str, object] = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.anthropic.com/v1",
                    "prompt_cache": {"enabled": False},
                },
            )

        self.assertTrue(response.ok)
        body = captured.get("body")
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        self.assertEqual(body.get("system"), "Static prefix")

    def test_openai_provider_encodes_local_image_attachment_as_image_url_part(self):
        provider = OpenAIProvider()
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "shot.png"
            image_path.write_bytes(b"png-bytes")
            request = LLMRequest.model_validate(
                {
                    "model": "gpt-4.1-mini",
                    "messages": [
                        {
                            "role": "user",
                            "content": "",
                            "content_parts": [
                                {"type": "text", "text": "inspect this"},
                                {
                                    "type": "image",
                                    "source": "path",
                                    "path": str(image_path),
                                    "mime_type": "image/png",
                                },
                            ],
                        }
                    ],
                }
            )
            payload = {
                "model": "gpt-4.1-mini",
                "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            }
            captured: dict[str, object] = {}

            def _fake_urlopen(http_request, timeout=None):
                del timeout
                captured["body"] = json.loads(http_request.data.decode("utf-8"))
                return _FakeHTTPResponse(payload)

            with patch(
                "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
                side_effect=_fake_urlopen,
            ):
                response = provider.complete(
                    request,
                    {
                        "api_key": "test-key",
                        "base_url": "https://api.openai.com/v1",
                        "enable_vision_input": True,
                    },
                )

        self.assertTrue(response.ok)
        body = captured.get("body")
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        messages = body.get("messages")
        self.assertIsInstance(messages, list)
        assert isinstance(messages, list)
        content = messages[0]["content"]
        self.assertIsInstance(content, list)
        assert isinstance(content, list)
        self.assertEqual(content[0], {"type": "text", "text": "inspect this"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(
            content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        )

    def test_anthropic_provider_encodes_local_image_attachment_as_image_block(self):
        provider = AnthropicProvider()
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "shot.png"
            image_path.write_bytes(b"png-bytes")
            request = LLMRequest.model_validate(
                {
                    "model": "claude-3-5-sonnet-latest",
                    "messages": [
                        {
                            "role": "user",
                            "content": "",
                            "content_parts": [
                                {"type": "text", "text": "inspect this"},
                                {
                                    "type": "image",
                                    "source": "path",
                                    "path": str(image_path),
                                    "mime_type": "image/png",
                                },
                            ],
                        }
                    ],
                }
            )
            payload = {
                "model": "claude-3-5-sonnet-latest",
                "content": [{"type": "text", "text": "hello"}],
                "usage": {"input_tokens": 3, "output_tokens": 2},
                "stop_reason": "end_turn",
            }
            captured: dict[str, object] = {}

            def _fake_urlopen(http_request, timeout=None):
                del timeout
                captured["body"] = json.loads(http_request.data.decode("utf-8"))
                return _FakeHTTPResponse(payload)

            with patch(
                "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
                side_effect=_fake_urlopen,
            ):
                response = provider.complete(
                    request,
                    {
                        "api_key": "test-key",
                        "base_url": "https://api.anthropic.com/v1",
                        "enable_vision_input": True,
                    },
                )

        self.assertTrue(response.ok)
        body = captured.get("body")
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        messages = body.get("messages")
        self.assertIsInstance(messages, list)
        assert isinstance(messages, list)
        content = messages[0]["content"]
        self.assertIsInstance(content, list)
        assert isinstance(content, list)
        self.assertEqual(content[0], {"type": "text", "text": "inspect this"})
        self.assertEqual(content[1]["type"], "image")
        self.assertEqual(content[1]["source"]["type"], "base64")
        self.assertEqual(content[1]["source"]["media_type"], "image/png")

    def test_ollama_provider_rejects_image_input_even_when_vision_toggle_enabled(self):
        provider = OllamaProvider()
        request = LLMRequest(
            model="llama3.1",
            messages=[
                {
                    "role": "user",
                    "content": "",
                    "content_parts": [
                        TextContentPart(text="inspect this"),
                        ImageContentPart(
                            source="base64",
                            mime_type="image/png",
                            data_base64="cG5n",
                        ),
                    ],
                }
            ],
        )
        with self.assertRaises(LLMCtlError) as exc:
            provider.complete(
                request,
                {
                    "base_url": "http://127.0.0.1:11434",
                    "enable_vision_input": True,
                },
            )

        self.assertEqual(exc.exception.code, "INVALID_ARGUMENT")

    def test_openai_official_minimax_recovers_structured_bracket_tool_envelope(
        self,
    ) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "MiniMax-M2.7",
                "messages": [{"role": "user", "content": "latest iran news"}],
                "tools": [
                    {
                        "name": "web.search",
                        "description": "Search current web/news information.",
                        "input_schema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    }
                ],
                "tool_choice": "auto",
            }
        )
        payload = {
            "model": "MiniMax-M2.7",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": (
                            "I'll research this.\n\n"
                            "[TOOL_CALL]\n"
                            '{tool => "web.search", args => { --query "latest Iran news May 2026" --top_n 10 }}\n'
                            "[/TOOL_CALL]"
                        )
                    },
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        }

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=_FakeHTTPResponse(payload),
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.minimax.io/v1",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.output_text, "")
        self.assertEqual(response.assistant_messages, [])
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].name, "web.search")
        self.assertEqual(
            response.tool_calls[0].arguments,
            {"query": "latest Iran news May 2026", "top_n": "10"},
        )
        normalization = dict(response.telemetry or {}).get("normalization", {})
        self.assertEqual(
            normalization.get("request_compat_profile"), "minimax_openai_compat"
        )
        self.assertEqual(normalization.get("tool_call_source"), "message.content")
        self.assertEqual(
            (normalization.get("tool_call_parse_metadata") or {}).get(
                "fallback_parse_mode"
            ),
            "minimax_bracket",
        )

    def test_openai_official_minimax_hides_think_blocks_from_visible_output(
        self,
    ) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "MiniMax-M2.7",
                "messages": [{"role": "user", "content": "hi"}],
            }
        )
        payload = {
            "model": "MiniMax-M2.7",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": (
                            "<think>\n"
                            "The user said hi, respond warmly.\n"
                            "</think>\n\n"
                            "Hi there!"
                        )
                    },
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=_FakeHTTPResponse(payload),
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.minimax.io/v1",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.output_text, "Hi there!")
        self.assertNotIn("<think>", response.output_text)

    def test_openai_dashscope_minimax_preserves_required_string_for_submit_output(
        self,
    ) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "MiniMax-M2.5",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [
                    {
                        "name": "submit_output",
                        "description": "return structured output",
                        "input_schema": {"type": "object"},
                    }
                ],
                "tool_choice": "required",
            }
        )
        payload = {
            "model": "MiniMax-M2.5",
            "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        captured: dict[str, object] = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://coding-intl.dashscope.aliyuncs.com/v1",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        body = captured.get("body")
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        self.assertEqual(body.get("tool_choice"), "required")

    def test_openai_standard_provider_preserves_submit_output_dict_tool_choice(
        self,
    ) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "gpt-4.1-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [
                    {
                        "name": "submit_output",
                        "description": "return structured output",
                        "input_schema": {"type": "object"},
                    }
                ],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "submit_output"},
                },
            }
        )
        payload = {
            "model": "gpt-4.1-mini",
            "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        captured: dict[str, object] = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.openai.com/v1",
                    "tool_call_strategy": "native",
                },
            )

        self.assertTrue(response.ok)
        body = captured.get("body")
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        self.assertEqual(
            body.get("tool_choice"),
            {"type": "function", "function": {"name": "submit_output"}},
        )

    def test_openai_dashscope_qwen_preserves_submit_output_target(self) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "qwen3.5-plus",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [
                    {
                        "name": "submit_output",
                        "description": "return structured output",
                        "input_schema": {"type": "object"},
                    }
                ],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "submit_output"},
                },
            }
        )
        payload = {
            "model": "qwen3.5-plus",
            "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        captured: dict[str, object] = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        body = captured.get("body")
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        self.assertEqual(
            body.get("tool_choice"),
            {"type": "function", "function": {"name": "submit_output"}},
        )

    def test_openai_dashscope_minimax_preserves_explicit_tool_choice_for_non_submit_output_on_success(
        self,
    ) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "MiniMax-M2.5",
                "messages": [{"role": "user", "content": "weather"}],
                "tools": [
                    {
                        "name": "weather",
                        "description": "lookup weather",
                        "input_schema": {"type": "object"},
                    }
                ],
                "tool_choice": {"type": "function", "function": {"name": "weather"}},
            }
        )
        payload = {
            "model": "MiniMax-M2.5",
            "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        captured: dict[str, object] = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://coding-intl.dashscope.aliyuncs.com/v1",
                    "tool_call_strategy": "native",
                },
            )

        self.assertTrue(response.ok)
        body = captured.get("body")
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        self.assertEqual(
            body.get("tool_choice"),
            {"type": "function", "function": {"name": "weather"}},
        )

    def test_openai_dashscope_minimax_retries_submit_output_target_to_auto_after_provider_error(
        self,
    ) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "MiniMax-M2.5",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [
                    {
                        "name": "submit_output",
                        "description": "return structured output",
                        "input_schema": {"type": "object"},
                    }
                ],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "submit_output"},
                },
            }
        )
        payload = {
            "model": "MiniMax-M2.5",
            "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        captured: list[dict[str, object]] = []

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            body = json.loads(http_request.data.decode("utf-8"))
            captured.append(body)
            if len(captured) == 1:
                error_payload = {
                    "error": {
                        "code": "invalid_parameter_error",
                        "message": (
                            "<400> InternalError.Algo.InvalidParameter: "
                            "The tool_choice parameter does not support being set "
                            "to required or object in thinking mode"
                        ),
                        "type": "invalid_request_error",
                    }
                }
                raise HTTPError(
                    url=http_request.full_url,
                    code=400,
                    msg="Bad Request",
                    hdrs=None,
                    fp=io.BytesIO(json.dumps(error_payload).encode("utf-8")),
                )
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://coding-intl.dashscope.aliyuncs.com/v1",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(len(captured), 2)
        self.assertEqual(
            captured[0].get("tool_choice"),
            {"type": "function", "function": {"name": "submit_output"}},
        )
        self.assertEqual(captured[1].get("tool_choice"), "auto")
        self.assertEqual(
            dict(response.telemetry or {})
            .get("normalization", {})
            .get("provider_retry_override"),
            "tool_choice_retry_to_auto",
        )

    def test_openai_dashscope_qwen_coding_intl_retries_submit_output_target_to_auto_after_provider_error(
        self,
    ) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "qwen3.5-plus",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [
                    {
                        "name": "submit_output",
                        "description": "return structured output",
                        "input_schema": {"type": "object"},
                    }
                ],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "submit_output"},
                },
            }
        )
        payload = {
            "model": "qwen3.5-plus",
            "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        captured: list[dict[str, object]] = []

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            body = json.loads(http_request.data.decode("utf-8"))
            captured.append(body)
            if len(captured) == 1:
                error_payload = {
                    "error": {
                        "code": "invalid_parameter_error",
                        "message": (
                            "<400> InternalError.Algo.InvalidParameter: "
                            "The tool_choice parameter does not support being set "
                            "to required or object in thinking mode"
                        ),
                        "type": "invalid_request_error",
                    }
                }
                raise HTTPError(
                    url=http_request.full_url,
                    code=400,
                    msg="Bad Request",
                    hdrs=None,
                    fp=io.BytesIO(json.dumps(error_payload).encode("utf-8")),
                )
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://coding-intl.dashscope.aliyuncs.com/v1",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(len(captured), 2)
        self.assertEqual(
            captured[0].get("tool_choice"),
            {"type": "function", "function": {"name": "submit_output"}},
        )
        self.assertEqual(captured[1].get("tool_choice"), "auto")
        self.assertEqual(
            dict(response.telemetry or {})
            .get("normalization", {})
            .get("provider_retry_override"),
            "tool_choice_retry_to_auto",
        )

    def test_openai_dashscope_minimax_recovers_submit_output_from_structured_xml_envelope(
        self,
    ) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "MiniMax-M2.5",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [
                    {
                        "name": "submit_output",
                        "description": "return structured output",
                        "input_schema": {"type": "object"},
                    }
                ],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "submit_output"},
                },
            }
        )
        payload = {
            "model": "MiniMax-M2.5",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": (
                            "<minimax:tool_call>"
                            '<invoke name="submit_output">'
                            '<param name="mode">respond</param>'
                            '<param name="confidence">1.0</param>'
                            '<param name="reason_code">greeting</param>'
                            '<param name="sub_intents">[]</param>'
                            '<param name="rationale"></param>'
                            '<param name="answer">hello</param>'
                            "</invoke>"
                            "</minimax:tool_call>"
                        )
                    },
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=_FakeHTTPResponse(payload),
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://coding-intl.dashscope.aliyuncs.com/v1",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].name, "submit_output")
        self.assertEqual(response.output_text, "")
        self.assertEqual(
            response.telemetry.get("normalization", {}).get("tool_call_source"),
            "message.content",
        )
        self.assertEqual(
            (
                response.telemetry.get("normalization", {}).get(
                    "tool_call_parse_metadata"
                )
                or {}
            ).get("fallback_parse_mode"),
            "minimax_xml",
        )

    def test_openai_minimax_retries_empty_payload_once_with_visible_output_instruction(
        self,
    ) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "MiniMax-M2.5",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [
                    {
                        "name": "web.search",
                        "description": "search",
                        "input_schema": {"type": "object"},
                    }
                ],
            }
        )
        captured: list[dict[str, object]] = []

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            body = json.loads(http_request.data.decode("utf-8"))
            captured.append(body)
            if len(captured) == 1:
                return _FakeHTTPResponse(
                    {
                        "model": "MiniMax-M2.5",
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {
                                    "content": "",
                                    "reasoning": "I found the answer.",
                                },
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 3,
                            "completion_tokens": 2,
                            "total_tokens": 5,
                        },
                    }
                )
            return _FakeHTTPResponse(
                {
                    "model": "MiniMax-M2.5",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": "visible answer"},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 4,
                        "completion_tokens": 3,
                        "total_tokens": 7,
                    },
                }
            )

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.minimax.io/v1",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.output_text, "visible answer")
        self.assertEqual(len(captured), 2)
        retry_messages = captured[1].get("messages") or []
        self.assertTrue(
            any(
                isinstance(item, dict)
                and str(item.get("role", "")).strip() == "system"
                and "Never leave the assistant message empty"
                in str(item.get("content", ""))
                for item in retry_messages
            )
        )
        normalization = dict(response.telemetry or {}).get("normalization", {})
        self.assertEqual(
            normalization.get("request_compat_profile"), "minimax_openai_compat"
        )
        self.assertIs(normalization.get("empty_payload_retry_used"), True)

    def test_openrouter_does_not_parse_fallback_tool_calls(self) -> None:
        provider = OpenRouterProvider()
        request = LLMRequest.model_validate(
            {
                "model": "openai/gpt-4.1-mini",
                "messages": [{"role": "user", "content": "weather"}],
                "tools": [
                    {"name": "weather", "description": "Lookup", "input_schema": {}}
                ],
            }
        )
        payload = {
            "model": "openai/gpt-4.1-mini",
            "choices": [
                {
                    "message": {
                        "content": '{"tool_calls":[{"name":"weather","arguments":{"location":"SF"}}]}'
                    }
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6},
        }

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=_FakeHTTPResponse(payload),
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://openrouter.ai/api/v1",
                    "tool_call_strategy": "fallback",
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.tool_calls, [])
        self.assertEqual(
            response.output_text,
            '{"tool_calls":[{"name":"weather","arguments":{"location":"SF"}}]}',
        )
        normalization = response.telemetry.get("normalization", {})
        self.assertEqual(normalization.get("tool_call_source"), "none")
        self.assertEqual(
            normalization.get("tool_call_skipped_fallback_sources"),
            ["message.content"],
        )

    def test_anthropic_parses_text_and_usage(self) -> None:
        provider = AnthropicProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "hello"}],
            }
        )
        payload = {
            "model": "claude-3-5-sonnet-latest",
            "content": [{"type": "text", "text": "Hello from Anthropic"}],
            "usage": {"input_tokens": 10, "output_tokens": 4},
        }

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=_FakeHTTPResponse(payload),
        ):
            response = provider.complete(
                request,
                {"api_key": "test-key", "base_url": "https://api.anthropic.com/v1"},
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.output_text, "Hello from Anthropic")
        self.assertEqual(response.usage.total_tokens, 14)

    def test_ollama_does_not_parse_fallback_tool_call(self) -> None:
        provider = OllamaProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "weather"}],
                "tools": [
                    {"name": "weather", "description": "Lookup", "input_schema": {}}
                ],
            }
        )
        payload = {
            "model": "llama3.1",
            "message": {
                "role": "assistant",
                "content": '{"tool_calls":[{"name":"weather","arguments":{"city":"LA"}}]}',
            },
            "prompt_eval_count": 11,
            "eval_count": 7,
        }

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=_FakeHTTPResponse(payload),
        ):
            response = provider.complete(
                request,
                {
                    "base_url": "http://127.0.0.1:11434",
                    "tool_call_strategy": "fallback",
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.usage.total_tokens, 18)
        self.assertEqual(response.tool_calls, [])
        self.assertEqual(
            response.output_text,
            '{"tool_calls":[{"name":"weather","arguments":{"city":"LA"}}]}',
        )
        normalization = response.telemetry.get("normalization", {})
        self.assertEqual(normalization.get("tool_call_source"), "none")
        self.assertEqual(
            normalization.get("tool_call_skipped_fallback_sources"),
            ["message.content"],
        )

    def test_ollama_schema_only_submit_output_uses_format_and_parses_json(self) -> None:
        provider = OllamaProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "decide"}],
                "tools": [
                    {
                        "name": "submit_output",
                        "description": "Submit structured output",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "mode": {"type": "string"},
                                "respond_kind": {"type": "string"},
                                "confidence": {"type": "number"},
                                "reason_code": {"type": "string"},
                            },
                            "required": [
                                "mode",
                                "respond_kind",
                                "confidence",
                                "reason_code",
                            ],
                            "additionalProperties": False,
                        },
                    }
                ],
                "tool_choice": {"name": "submit_output"},
            }
        )
        payload = {
            "model": "kimi-k2.5",
            "message": {
                "role": "assistant",
                "content": (
                    '{"mode":"respond","respond_kind":"answer",'
                    '"confidence":0.98,"reason_code":"greeting"}'
                ),
            },
            "prompt_eval_count": 20,
            "eval_count": 8,
        }
        captured: dict[str, Any] = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "base_url": "https://ollama.com",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(captured["body"]["format"]["type"], "object")
        self.assertNotIn("tools", captured["body"])
        instruction_messages = [
            str(item.get("content", ""))
            for item in captured["body"]["messages"]
            if item.get("role") == "system"
        ]
        combined_instruction = "\n".join(instruction_messages)
        self.assertIn("Allowed keys: mode, respond_kind", combined_instruction)
        self.assertIn(
            "Required keys: mode, respond_kind, confidence, reason_code.",
            combined_instruction,
        )
        self.assertIn(
            "Type hints: mode=string; respond_kind=string; confidence=number; reason_code=string.",
            combined_instruction,
        )
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].name, "submit_output")
        self.assertEqual(response.tool_calls[0].arguments["mode"], "respond")
        self.assertEqual(
            response.telemetry.get("normalization", {}).get(
                "schema_only_submit_output"
            ),
            True,
        )

    def test_ollama_hybrid_sends_native_tools_and_parses_native_tool_calls(
        self,
    ) -> None:
        provider = OllamaProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "weather"}],
                "tools": [
                    {"name": "weather", "description": "Lookup", "input_schema": {}}
                ],
                "tool_choice": "auto",
            }
        )
        payload = {
            "model": "qwen3.5:397b",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "weather",
                            "arguments": {"city": "Tokyo"},
                        }
                    }
                ],
            },
            "prompt_eval_count": 15,
            "eval_count": 4,
        }
        captured: dict[str, Any] = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "base_url": "https://ollama.com",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        self.assertIn("tools", captured["body"])
        self.assertEqual(captured["body"]["tools"][0]["function"]["name"], "weather")
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].arguments.get("city"), "Tokyo")
        self.assertEqual(
            response.telemetry.get("normalization", {}).get("tool_call_source"),
            "native",
        )

    def test_ollama_preserves_native_tool_calls_from_minimax_trace_fixtures(
        self,
    ) -> None:
        provider = OllamaProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "what time is it in UTC?"}],
                "tools": [
                    {"name": "time", "description": "Lookup time", "input_schema": {}}
                ],
                "tool_choice": "auto",
            }
        )

        for fixture_name, expected_model in (
            ("ollamacloud_minimax_m2_5_step01_call01.json", "minimax-m2.5"),
            ("ollamacloud_minimax_m2_7_step01_call01.json", "minimax-m2.7"),
        ):
            with self.subTest(fixture=fixture_name):
                payload = _load_fixture_payload(fixture_name)
                with patch(
                    "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
                    return_value=_FakeHTTPResponse(payload),
                ):
                    response = provider.complete(
                        request,
                        {
                            "base_url": "https://ollama.com",
                            "tool_call_strategy": "hybrid",
                        },
                    )

                self.assertTrue(response.ok)
                self.assertEqual(response.model, expected_model)
                self.assertEqual(len(response.tool_calls), 1)
                self.assertEqual(len(response.thinking), 1)
                self.assertTrue(response.thinking[0]["content"])
                self.assertEqual(response.tool_calls[0].name, "time")
                self.assertIsNone(response.tool_calls[0].arguments.get("timezone"))
                normalization = response.telemetry.get("normalization", {})
                self.assertEqual(normalization.get("tool_call_source"), "native")
                self.assertEqual(normalization.get("tool_call_native_call_count"), 1)
                self.assertEqual(
                    normalization.get("tool_call_skipped_fallback_sources"),
                    ["message.thinking"],
                )

    def test_anthropic_preserves_thinking_content_blocks(self) -> None:
        provider = AnthropicProvider()
        request = LLMRequest.model_validate(
            {"messages": [{"role": "user", "content": "think before answering"}]}
        )
        payload = {
            "model": "claude-4.6",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "I should inspect the request carefully.",
                },
                {"type": "text", "text": "Done"},
            ],
            "usage": {"input_tokens": 4, "output_tokens": 6},
            "stop_reason": "end_turn",
        }

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=_FakeHTTPResponse(payload),
        ):
            response = provider.complete(
                request,
                {"api_key": "test-key", "base_url": "https://api.anthropic.com/v1"},
            )

        assert len(response.thinking) == 1
        assert (
            response.thinking[0]["content"] == "I should inspect the request carefully."
        )

    def test_ollama_maps_minimal_thinking_profile_to_think_false(self) -> None:
        provider = OllamaProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "hello"}],
                "metadata": {
                    "thinking": "minimal",
                    "thinking_reasoning_profile": "minimal",
                },
            }
        )
        payload = {
            "model": "kimi-k2.5",
            "message": {"role": "assistant", "content": "hello"},
            "prompt_eval_count": 5,
            "eval_count": 2,
        }
        captured: dict[str, Any] = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(payload)

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "base_url": "https://ollama.com",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(captured["body"]["think"], False)

    def test_ollama_hides_think_blocks_from_visible_output(self) -> None:
        provider = OllamaProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "hello"}],
            }
        )
        payload = {
            "model": "kimi-k2.5",
            "message": {
                "role": "assistant",
                "content": (
                    "<think>\nThe user is greeting us.\n</think>\n\nhello from ollama"
                ),
            },
            "prompt_eval_count": 5,
            "eval_count": 2,
        }

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=_FakeHTTPResponse(payload),
        ):
            response = provider.complete(
                request,
                {
                    "base_url": "https://ollama.com",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.output_text, "hello from ollama")
        self.assertNotIn("<think>", response.output_text)

    def test_cortensor_provider_completion_mode_maps_response(self) -> None:
        provider = CortensorProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "hello"}],
            }
        )
        captured = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["url"] = str(getattr(http_request, "full_url", ""))
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(
                {
                    "model": "gpt-4.1-mini",
                    "choices": [{"text": "hello from cortensor"}],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 4,
                        "total_tokens": 9,
                    },
                }
            )

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "model": "gpt-4.1-mini",
                    "base_url": "https://router.example/api/v1/completions",
                    "api_mode": "cortensor_completion",
                    "session_id": 35,
                    "max_tokens": 1024,
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.model, "gpt-4.1-mini")
        self.assertEqual(response.output_text, "hello from cortensor")
        self.assertEqual(response.usage.total_tokens, 9)
        self.assertEqual(captured["url"], "https://router.example/api/v2/completions")
        self.assertEqual(captured["body"]["session_id"], 35)
        self.assertGreaterEqual(int(captured["body"]["max_tokens"]), 4096)

    def test_openrouter_provider_collapses_qwen_system_messages(self) -> None:
        provider = OpenRouterProvider()
        request = LLMRequest.model_validate(
            {
                "model": "qwen/qwen3.5-35b-a3b",
                "messages": [
                    {"role": "system", "content": "Primary system context."},
                    {"role": "system", "content": "Structured tool contract."},
                    {"role": "user", "content": "what time is it in UTC?"},
                ],
            }
        )
        captured = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(
                {
                    "model": "qwen/qwen3.5-35b-a3b",
                    "choices": [
                        {
                            "message": {"content": "hello from qwen"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 4,
                        "total_tokens": 9,
                    },
                }
            )

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://openrouter.ai/api/v1",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        messages = captured["body"]["messages"]
        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        self.assertIn("Primary system context.", messages[0]["content"])
        self.assertIn("Structured tool contract.", messages[0]["content"])

    def test_openai_provider_collapses_dashscope_minimax_system_messages(self) -> None:
        provider = OpenAIProvider()
        request = LLMRequest.model_validate(
            {
                "model": "MiniMax-M2.5",
                "messages": [
                    {"role": "system", "content": "Primary system context."},
                    {"role": "system", "content": "Structured tool contract."},
                    {"role": "user", "content": "what time is it in UTC?"},
                ],
            }
        )
        captured = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(
                {
                    "model": "MiniMax-M2.5",
                    "choices": [
                        {
                            "message": {"content": "hello from minimax"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 4,
                        "total_tokens": 9,
                    },
                }
            )

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://coding-intl.dashscope.aliyuncs.com/v1",
                    "tool_call_strategy": "hybrid",
                },
            )

        self.assertTrue(response.ok)
        messages = captured["body"]["messages"]
        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        self.assertIn("Primary system context.", messages[0]["content"])
        self.assertIn("Structured tool contract.", messages[0]["content"])

    def test_cortensor_completion_prompt_merges_system_sections(self) -> None:
        provider = CortensorProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [
                    {
                        "role": "system",
                        "content": "You are OpenMinion, a pragmatic assistant.",
                    },
                    {
                        "role": "system",
                        "content": "Agent canonical memory (cross-session): ...",
                    },
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                    {"role": "user", "content": "what's weather in hong kong?"},
                ],
                "tools": [
                    {
                        "name": "weather",
                        "description": "Lookup weather",
                        "input_schema": {},
                    }
                ],
                "metadata": {"tool_call_strategy": "hybrid"},
            }
        )

        prompt = provider._build_completion_prompt(request)  # noqa: SLF001 - intentional unit coverage

        self.assertIn(
            "System instruction:\nYou are OpenMinion, a pragmatic assistant.", prompt
        )
        self.assertIn("Agent canonical memory (cross-session): ...", prompt)
        self.assertIn("Tool-calling contract:", prompt)
        self.assertNotIn("Conversation history:\nsystem:", prompt)
        self.assertIn("user: what's weather in hong kong?", prompt)
        self.assertTrue(prompt.rstrip().endswith("assistant:"))

    def test_cortensor_provider_auto_mode_falls_back_to_openai_chat_on_empty_completion_text(
        self,
    ) -> None:
        provider = CortensorProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "hello"}],
            }
        )
        captured_urls: list[str] = []

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            url = str(getattr(http_request, "full_url", ""))
            captured_urls.append(url)
            if url.endswith("/api/v2/completions"):
                return _FakeHTTPResponse({"choices": [{"text": ""}]})
            if url.endswith("/api/v2/chat/completions"):
                return _FakeHTTPResponse(
                    {
                        "model": "gpt-4.1-mini",
                        "choices": [{"message": {"content": "fallback chat text"}}],
                        "usage": {
                            "prompt_tokens": 5,
                            "completion_tokens": 4,
                            "total_tokens": 9,
                        },
                    }
                )
            raise AssertionError(f"Unexpected URL in test: {url}")

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "model": "gpt-4.1-mini",
                    "base_url": "https://router.example/api/v2/completions",
                    "api_mode": "auto",
                    "session_id": 35,
                    "result_wait_attempts": 1,
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.output_text, "fallback chat text")
        self.assertEqual(
            captured_urls,
            [
                "https://router.example/api/v2/completions",
                "https://router.example/api/v2/chat/completions",
            ],
        )

    def test_cortensor_provider_explicit_completion_mode_does_not_fallback_to_openai_chat(
        self,
    ) -> None:
        provider = CortensorProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "hello"}],
            }
        )
        captured_urls: list[str] = []

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            url = str(getattr(http_request, "full_url", ""))
            captured_urls.append(url)
            return _FakeHTTPResponse({"choices": [{"text": ""}]})

        with (
            patch(
                "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
                side_effect=_fake_urlopen,
            ),
            self.assertRaisesRegex(LLMCtlError, "did not include text or tool calls"),
        ):
            provider.complete(
                request,
                {
                    "model": "gpt-4.1-mini",
                    "base_url": "https://router.example/api/v2/completions",
                    "api_mode": "cortensor_completion",
                    "session_id": 35,
                    "result_wait_attempts": 1,
                },
            )

        self.assertEqual(captured_urls, ["https://router.example/api/v2/completions"])

    def test_cortensor_provider_retries_empty_completion_payload_before_failing(
        self,
    ) -> None:
        provider = CortensorProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "hello"}],
            }
        )
        attempts = {"count": 0}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            attempts["count"] += 1
            if attempts["count"] == 1:
                return _FakeHTTPResponse({"choices": [{"text": ""}]})
            return _FakeHTTPResponse(
                {
                    "model": "gpt-4.1-mini",
                    "choices": [{"text": "hello after retry"}],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 4,
                        "total_tokens": 9,
                    },
                }
            )

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "model": "gpt-4.1-mini",
                    "base_url": "https://router.example/api/v2/completions",
                    "api_mode": "cortensor_completion",
                    "session_id": 35,
                    "result_wait_attempts": 2,
                    "result_wait_interval_seconds": 0,
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.output_text, "hello after retry")
        self.assertEqual(attempts["count"], 2)

    def test_cortensor_provider_extends_retries_for_offchain_result_pending(
        self,
    ) -> None:
        provider = CortensorProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "hello"}],
            }
        )
        attempts = {"count": 0}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            attempts["count"] += 1
            if attempts["count"] < 4:
                return _FakeHTTPResponse(
                    {
                        "status": "ended",
                        "message": "No content",
                        "result_data": "urn:blob:v2:s3:akamai:test0:abc_result",
                    }
                )
            return _FakeHTTPResponse(
                {
                    "model": "gpt-4.1-mini",
                    "choices": [{"text": "available after offchain delay"}],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 4,
                        "total_tokens": 9,
                    },
                }
            )

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "model": "gpt-4.1-mini",
                    "base_url": "https://router.example/api/v2/completions",
                    "api_mode": "cortensor_completion",
                    "session_id": 35,
                    "result_wait_attempts": 2,
                    "result_wait_interval_seconds": 0,
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.output_text, "available after offchain delay")
        # Confirms retry budget extended beyond configured result_wait_attempts=2.
        self.assertEqual(attempts["count"], 4)

    def test_cortensor_provider_extends_retries_when_offchain_urn_has_no_text(
        self,
    ) -> None:
        provider = CortensorProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "hello"}],
            }
        )
        attempts = {"count": 0}

        def _fake_urlopen(http_request, timeout=None):
            del http_request, timeout
            attempts["count"] += 1
            if attempts["count"] < 4:
                return _FakeHTTPResponse(
                    {
                        "status": "ended",
                        "result_data": "urn:blob:v2:s3:akamai:test0:abc_result",
                    }
                )
            return _FakeHTTPResponse(
                {
                    "model": "gpt-4.1-mini",
                    "choices": [{"text": "available after urn polling"}],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 4,
                        "total_tokens": 9,
                    },
                }
            )

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "model": "gpt-4.1-mini",
                    "base_url": "https://router.example/api/v2/completions",
                    "api_mode": "cortensor_completion",
                    "session_id": 35,
                    "result_wait_attempts": 2,
                    "result_wait_interval_seconds": 0,
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.output_text, "available after urn polling")
        # Confirms retry budget extended beyond configured result_wait_attempts=2.
        self.assertEqual(attempts["count"], 4)

    def test_cortensor_provider_respects_request_max_output_tokens(self) -> None:
        provider = CortensorProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "hello"}],
                "max_output_tokens": 6000,
            }
        )
        captured = {}

        def _fake_urlopen(http_request, timeout=None):
            del timeout
            captured["body"] = json.loads(http_request.data.decode("utf-8"))
            return _FakeHTTPResponse(
                {
                    "model": "gpt-4.1-mini",
                    "choices": [{"text": "hello from cortensor"}],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 4,
                        "total_tokens": 9,
                    },
                }
            )

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "model": "gpt-4.1-mini",
                    "base_url": "https://router.example/api/v2/completions",
                    "api_mode": "cortensor_completion",
                    "session_id": 35,
                    "max_tokens": 4096,
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(captured["body"]["max_tokens"], 6000)

    def test_cortensor_empty_payload_error_code(self) -> None:
        provider = CortensorProvider()
        request = LLMRequest.model_validate(
            {"messages": [{"role": "user", "content": "hello"}]}
        )

        def _fake_urlopen(http_request, timeout=None):
            del timeout, http_request
            return _FakeHTTPResponse(
                {
                    "model": "gpt-4.1-mini",
                    "choices": [{"message": {"role": "assistant", "content": ""}}],
                }
            )

        with (
            patch(
                "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
                side_effect=_fake_urlopen,
            ),
            self.assertRaises(LLMCtlError) as ctx,
        ):
            provider.complete(
                request,
                {
                    "model": "gpt-4.1-mini",
                    "base_url": "http://localhost:8080/api/v2/completions",
                    "api_mode": "openai_chat",
                },
            )

        self.assertEqual(ctx.exception.code, "EMPTY_PAYLOAD")
        self.assertTrue(ctx.exception.details.get("retryable"))

    def test_cortensor_malformed_payload_error_code(self) -> None:
        provider = CortensorProvider()
        request = LLMRequest.model_validate(
            {"messages": [{"role": "user", "content": "hello"}]}
        )

        def _fake_urlopen(http_request, timeout=None):
            del timeout, http_request
            return _FakeHTTPResponse(
                {
                    "model": "gpt-4.1-mini",
                    "choices": [{"message": None}],
                }
            )

        with (
            patch(
                "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
                side_effect=_fake_urlopen,
            ),
            self.assertRaises(LLMCtlError) as ctx,
        ):
            provider.complete(
                request,
                {
                    "model": "gpt-4.1-mini",
                    "base_url": "http://localhost:8080/api/v2/completions",
                    "api_mode": "openai_chat",
                },
            )

        self.assertEqual(ctx.exception.code, "MALFORMED_PAYLOAD")
        self.assertFalse(ctx.exception.details.get("retryable"))

    def test_cortensor_tool_call_only_valid(self) -> None:
        provider = CortensorProvider()
        request = LLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "hello"}],
                "tools": [{"name": "weather", "description": "test tool"}],
            }
        )

        def _fake_urlopen(http_request, timeout=None):
            del timeout, http_request
            return _FakeHTTPResponse(
                {
                    "model": "gpt-4.1-mini",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "call_123",
                                        "type": "function",
                                        "function": {
                                            "name": "weather",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                }
            )

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            response = provider.complete(
                request,
                {
                    "model": "gpt-4.1-mini",
                    "base_url": "http://localhost:8080/api/v2/completions",
                    "api_mode": "openai_chat",
                },
            )

        self.assertTrue(response.ok)
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].name, "weather")
        self.assertEqual(
            response.telemetry.get("normalization", {}).get("tool_call_source"),
            "native",
        )
