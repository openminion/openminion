from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from openminion.modules.llm.providers.adapters import OpenRouterProvider
from openminion.modules.llm.schemas import LLMRequest


# Helpers


def _provider() -> OpenRouterProvider:
    return OpenRouterProvider()


def _config(*, api_key: str = "test-key", model: str = "openai/gpt-4.1-mini") -> dict:
    return {
        "api_key": api_key,
        "base_url": "https://openrouter.ai/api/v1",
        "model": model,
        "timeout_seconds": 10,
    }


def _request(
    *,
    content: str = "Hello",
    tools: list | None = None,
    tool_choice: str | dict | None = None,
    max_output_tokens: int | None = None,
) -> LLMRequest:
    payload: dict = {
        "messages": [{"role": "user", "content": content}],
        "tools": tools,
    }
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if max_output_tokens is not None:
        payload["max_output_tokens"] = max_output_tokens
    return LLMRequest.model_validate(payload)


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


class _FakeSSEHTTPResponse:
    def __init__(self, lines: list[str]):
        encoded = "\n".join(lines).encode("utf-8") + b"\n"
        self._file = io.BytesIO(encoded)

    def __iter__(self):
        return self._file

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


def _openrouter_json_response(
    text: str = "Hello from OpenRouter",
    model: str = "openai/gpt-4.1-mini",
    tool_calls: list | None = None,
    cost: float | None = None,
) -> dict:
    message: dict = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage: dict = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    if cost is not None:
        usage["cost"] = cost
    return {
        "model": model,
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": usage,
    }


def _http_error(code: int):
    from urllib.error import HTTPError

    err = HTTPError(
        url="https://openrouter.ai/api/v1/chat/completions",
        code=code,
        msg=f"HTTP {code}",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b'{"error": "test error"}'),
    )
    return err


# Full-chain complete() tests


class TestOpenRouterFullChainComplete(unittest.TestCase):
    def test_basic_text_response(self):
        provider = _provider()
        fake_resp = _FakeHTTPResponse(_openrouter_json_response("Hi there!"))
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=fake_resp,
        ):
            result = provider.complete(_request(content="Hello"), _config())
        self.assertTrue(result.ok)
        self.assertEqual(result.output_text, "Hi there!")
        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(result.model, "openai/gpt-4.1-mini")

    def test_usage_populated(self):
        provider = _provider()
        fake_resp = _FakeHTTPResponse(_openrouter_json_response("ok"))
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=fake_resp,
        ):
            result = provider.complete(_request(), _config())
        self.assertEqual(result.usage.input_tokens, 10)
        self.assertEqual(result.usage.output_tokens, 5)
        self.assertEqual(result.usage.total_tokens, 15)

    def test_cost_usd_populated_when_present(self):
        provider = _provider()
        fake_resp = _FakeHTTPResponse(_openrouter_json_response("ok", cost=0.000123))
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=fake_resp,
        ):
            result = provider.complete(_request(), _config())
        self.assertIsNotNone(result.cost_usd)
        self.assertAlmostEqual(result.cost_usd, 0.000123, places=7)

    def test_cost_usd_none_when_absent(self):
        provider = _provider()
        fake_resp = _FakeHTTPResponse(_openrouter_json_response("ok", cost=None))
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=fake_resp,
        ):
            result = provider.complete(_request(), _config())
        self.assertIsNone(result.cost_usd)

    def test_native_tool_calls_parsed(self):
        provider = _provider()
        native_tc = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "weather", "arguments": '{"location": "SF"}'},
            }
        ]
        tools = [{"name": "weather", "description": "Get weather", "input_schema": {}}]
        fake_resp = _FakeHTTPResponse(
            _openrouter_json_response("", tool_calls=native_tc)
        )
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=fake_resp,
        ):
            result = provider.complete(_request(tools=tools), _config())
        self.assertTrue(result.ok)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].name, "weather")
        self.assertEqual(result.tool_calls[0].arguments.get("location"), "SF")

    def test_claude_openrouter_normalizes_outbound_tool_names_and_remaps_inbound(
        self,
    ):
        provider = _provider()
        captured_request: dict[str, object] = {}

        def _fake_urlopen(request_obj, timeout=None):
            del timeout
            captured_request["payload"] = json.loads(request_obj.data.decode("utf-8"))
            return _FakeHTTPResponse(
                _openrouter_json_response(
                    text="",
                    model="anthropic/claude-3.5-haiku",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "arguments": '{"query":"latest news iran"}',
                            },
                        }
                    ],
                )
            )

        tools = [
            {"name": "web.search", "description": "Search the web", "input_schema": {}}
        ]
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            result = provider.complete(
                _request(
                    tools=tools,
                    tool_choice={
                        "type": "function",
                        "function": {"name": "web.search"},
                    },
                ),
                _config(model="anthropic/claude-3.5-haiku"),
            )

        self.assertTrue(result.ok)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].name, "web.search")
        payload = captured_request["payload"]
        assert isinstance(payload, dict)
        tools_payload = payload.get("tools")
        assert isinstance(tools_payload, list)
        self.assertEqual(tools_payload[0]["function"]["name"], "web_search")
        self.assertEqual(
            payload.get("tool_choice"),
            {"type": "function", "function": {"name": "web_search"}},
        )
        messages = payload.get("messages")
        assert isinstance(messages, list)
        self.assertIn("- web_search: Search the web", messages[0]["content"])

    def test_reasoning_fallback_tool_calls_parsed_when_content_is_empty(self):
        provider = _provider()
        tools = [
            {"name": "web.search", "description": "Search the web", "input_schema": {}}
        ]
        payload = _openrouter_json_response(text="")
        payload["choices"][0]["message"]["content"] = None
        payload["choices"][0]["message"]["reasoning"] = (
            '{"tool_calls":[{"name":"web.search","arguments":{"query":"latest news iran"}}]}'
        )
        fake_resp = _FakeHTTPResponse(payload)
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=fake_resp,
        ):
            result = provider.complete(_request(tools=tools), _config())
        self.assertTrue(result.ok)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].name, "web.search")
        self.assertEqual(
            result.tool_calls[0].arguments.get("query"), "latest news iran"
        )

    def test_claude_openrouter_fallback_tool_calls_are_remapped_to_canonical_names(
        self,
    ):
        provider = _provider()
        tools = [
            {"name": "web.search", "description": "Search the web", "input_schema": {}}
        ]
        payload = _openrouter_json_response(text="", model="anthropic/claude-3.5-haiku")
        payload["choices"][0]["message"]["content"] = None
        payload["choices"][0]["message"]["reasoning"] = (
            '{"tool_calls":[{"name":"web_search","arguments":{"query":"latest news iran"}}]}'
        )
        fake_resp = _FakeHTTPResponse(payload)
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=fake_resp,
        ):
            result = provider.complete(
                _request(tools=tools),
                _config(model="anthropic/claude-3.5-haiku"),
            )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].name, "web.search")
        self.assertEqual(
            result.tool_calls[0].arguments.get("query"), "latest news iran"
        )

    def test_refusal_text_used_when_content_is_empty(self):
        provider = _provider()
        payload = _openrouter_json_response(text="")
        payload["choices"][0]["message"]["content"] = None
        payload["choices"][0]["message"]["refusal"] = "I am unable to provide that."
        fake_resp = _FakeHTTPResponse(payload)
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=fake_resp,
        ):
            result = provider.complete(_request(content="latest war news"), _config())
        self.assertTrue(result.ok)
        self.assertEqual(result.output_text, "I am unable to provide that.")

    def test_choice_text_used_when_message_content_is_empty(self):
        provider = _provider()
        payload = _openrouter_json_response(text="")
        payload["choices"][0]["message"]["content"] = None
        payload["choices"][0]["text"] = "fallback text from choice"
        fake_resp = _FakeHTTPResponse(payload)
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=fake_resp,
        ):
            result = provider.complete(_request(content="hello"), _config())
        self.assertTrue(result.ok)
        self.assertEqual(result.output_text, "fallback text from choice")
        self.assertEqual(
            result.telemetry.get("normalization", {}).get("text_source"), "choice.text"
        )

    def test_top_level_response_text_used_when_message_shape_varies(self):
        provider = _provider()
        payload = _openrouter_json_response(text="")
        payload["choices"][0]["message"] = {"role": "assistant", "content": None}
        payload["response"] = "fallback from top-level response"
        fake_resp = _FakeHTTPResponse(payload)
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=fake_resp,
        ):
            result = provider.complete(_request(content="hello"), _config())
        self.assertTrue(result.ok)
        self.assertEqual(result.output_text, "fallback from top-level response")
        self.assertEqual(
            result.telemetry.get("normalization", {}).get("text_source"),
            "response.response",
        )

    def test_latency_ms_populated(self):
        provider = _provider()
        fake_resp = _FakeHTTPResponse(_openrouter_json_response("ok"))
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=fake_resp,
        ):
            result = provider.complete(_request(), _config())
        self.assertGreaterEqual(result.latency_ms, 0)

    def test_config_max_tokens_applied_when_request_omits_max_output_tokens(self):
        provider = _provider()
        fake_resp = _FakeHTTPResponse(_openrouter_json_response("ok"))
        captured: dict[str, dict] = {}

        def _fake_urlopen(request_obj, timeout=None):  # noqa: ARG001
            raw = getattr(request_obj, "data", b"") or b""
            captured["json"] = json.loads(raw.decode("utf-8"))
            return fake_resp

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            provider.complete(_request(), {**_config(), "max_tokens": 512})

        self.assertEqual(captured["json"]["max_tokens"], 512)

    def test_request_max_output_tokens_overrides_config_max_tokens(self):
        provider = _provider()
        fake_resp = _FakeHTTPResponse(_openrouter_json_response("ok"))
        captured: dict[str, dict] = {}

        def _fake_urlopen(request_obj, timeout=None):  # noqa: ARG001
            raw = getattr(request_obj, "data", b"") or b""
            captured["json"] = json.loads(raw.decode("utf-8"))
            return fake_resp

        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_fake_urlopen,
        ):
            provider.complete(
                _request(max_output_tokens=333),
                {**_config(), "max_tokens": 512},
            )

        self.assertEqual(captured["json"]["max_tokens"], 333)


# Error handling tests


class TestOpenRouterErrorHandling(unittest.TestCase):
    def _assert_raises_llmctl_error(self, http_code: int, expected_code: str):
        from openminion.modules.llm.errors import LLMCtlError

        provider = _provider()
        with (
            patch(
                "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
                side_effect=_http_error(http_code),
            ),
            self.assertRaises(LLMCtlError) as ctx,
        ):
            provider.complete(_request(), _config())
        self.assertEqual(ctx.exception.code, expected_code)

    def test_401_raises_auth_error(self):
        self._assert_raises_llmctl_error(401, "AUTH_ERROR")

    def test_403_raises_auth_error(self):
        self._assert_raises_llmctl_error(403, "AUTH_ERROR")

    def test_429_raises_rate_limited(self):
        self._assert_raises_llmctl_error(429, "RATE_LIMITED")

    def test_500_raises_provider_error(self):
        self._assert_raises_llmctl_error(500, "PROVIDER_ERROR")

    def test_timeout_raises_timeout_error(self):
        import socket
        from urllib.error import URLError
        from openminion.modules.llm.errors import LLMCtlError

        provider = _provider()
        url_error = URLError(socket.timeout("timed out"))
        with (
            patch(
                "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
                side_effect=url_error,
            ),
            self.assertRaises(LLMCtlError) as ctx,
        ):
            provider.complete(_request(), _config())
        self.assertEqual(ctx.exception.code, "TIMEOUT")

    def test_missing_api_key_raises_auth_error(self):
        from openminion.modules.llm.errors import LLMCtlError

        provider = _provider()
        with self.assertRaises(LLMCtlError) as ctx:
            provider.complete(
                _request(), {"api_key": "", "base_url": "https://openrouter.ai/api/v1"}
            )
        self.assertEqual(ctx.exception.code, "AUTH_ERROR")


# Streaming tests


class TestOpenRouterStreaming(unittest.TestCase):
    def _sse_lines(self, chunks: list[str], include_done: bool = True) -> list[str]:
        lines = [
            f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}"
            for c in chunks
        ]
        if include_done:
            lines.append("data: [DONE]")
        return lines

    def test_stream_yields_deltas(self):
        provider = _provider()
        sse_lines = self._sse_lines(["Hello", " world"])
        fake_resp = _FakeSSEHTTPResponse(sse_lines)
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=fake_resp,
        ):
            events = list(provider.stream(_request(), _config()))
        delta_events = [e for e in events if e.type == "delta"]
        self.assertEqual(len(delta_events), 2)
        self.assertEqual(delta_events[0].delta_text, "Hello")
        self.assertEqual(delta_events[1].delta_text, " world")

    def test_stream_ends_with_done(self):
        provider = _provider()
        fake_resp = _FakeSSEHTTPResponse(self._sse_lines(["hi"]))
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=fake_resp,
        ):
            events = list(provider.stream(_request(), _config()))
        self.assertEqual(events[-1].type, "done")

    def test_stream_auth_error(self):
        provider = _provider()
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_http_error(401),
        ):
            events = list(provider.stream(_request(), _config()))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, "error")
        self.assertEqual(events[0].error.code, "AUTH_ERROR")

    def test_stream_rate_limited(self):
        provider = _provider()
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=_http_error(429),
        ):
            events = list(provider.stream(_request(), _config()))
        self.assertEqual(events[0].type, "error")
        self.assertEqual(events[0].error.code, "RATE_LIMITED")

    def test_stream_missing_api_key_yields_error(self):
        provider = _provider()
        events = list(provider.stream(_request(), {"api_key": ""}))
        self.assertEqual(events[0].type, "error")
        self.assertEqual(events[0].error.code, "AUTH_ERROR")

    def test_stream_skips_non_data_lines(self):
        provider = _provider()
        raw_lines = [
            ": keep-alive",
            "",
            'data: {"choices": [{"delta": {"content": "hi"}}]}',
            "data: [DONE]",
        ]
        fake_resp = _FakeSSEHTTPResponse(raw_lines)
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=fake_resp,
        ):
            events = list(provider.stream(_request(), _config()))
        delta_events = [e for e in events if e.type == "delta"]
        self.assertEqual(len(delta_events), 1)
        self.assertEqual(delta_events[0].delta_text, "hi")


# list_models() tests


class TestOpenRouterListModels(unittest.TestCase):
    def test_list_models_from_api(self):
        provider = _provider()
        models_payload = {
            "data": [
                {"id": "openai/gpt-4.1-mini"},
                {"id": "anthropic/claude-3.5-sonnet"},
                {"id": "meta-llama/llama-3.1-70b"},
            ]
        }
        fake_resp = _FakeHTTPResponse(models_payload)
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            return_value=fake_resp,
        ):
            models = provider.list_models({"api_key": "test-key"})
        self.assertIn("openai/gpt-4.1-mini", models)
        self.assertIn("anthropic/claude-3.5-sonnet", models)
        self.assertEqual(len(models), 3)

    def test_list_models_empty_without_api_key(self):
        provider = _provider()
        models = provider.list_models({"api_key": ""})
        self.assertEqual(models, [])

    def test_list_models_empty_on_api_error(self):
        provider = _provider()
        with patch(
            "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
            side_effect=Exception("network error"),
        ):
            models = provider.list_models({"api_key": "test-key"})
        self.assertEqual(models, [])
