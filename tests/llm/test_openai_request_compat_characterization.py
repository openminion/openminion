from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from openminion.modules.llm.config import resolve_provider_identity_translation
from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.providers.behavior.resolver import resolve_behavior_profile
from openminion.modules.llm.providers.adapters import OpenAIProvider
from openminion.modules.llm.setup_catalog import get_setup_preset
from openminion.modules.llm.schemas import LLMRequest


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        return None


def _complete_with_payload(
    *, model: str, base_url: str
) -> tuple[str | None, str, str, dict[str, object]]:
    provider = OpenAIProvider()
    request = LLMRequest.model_validate(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": "Primary system context."},
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
        "model": model,
        "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    captured: dict[str, object] = {}

    def _fake_urlopen(http_request, timeout=None):
        del timeout
        captured["url"] = http_request.full_url
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
                "base_url": base_url,
                "tool_call_strategy": "hybrid",
            },
        )

    assert response.ok
    body = captured.get("body")
    assert isinstance(body, dict)
    messages = body.get("messages")
    assert isinstance(messages, list)
    rendered = "\n".join(
        str(item.get("content", "") or "")
        for item in messages
        if isinstance(item, dict)
    )
    profile = (
        dict(response.telemetry or {})
        .get("normalization", {})
        .get("request_compat_profile")
    )
    captured_url = str(captured.get("url") or "")
    return profile, rendered, captured_url, body


@pytest.mark.parametrize(
    ("model", "base_url", "expected_profile", "expected_label"),
    [
        (
            "MiniMax-M2.7",
            "https://api.minimax.io/v1",
            "minimax_openai_compat",
            "Native tool-calling contract:",
        ),
        (
            "MiniMax-M2.5",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "minimax_openai_compat",
            "Native tool-calling contract:",
        ),
        (
            "MiniMax-M2.7",
            "https://api.openai.com/v1",
            "openai_default",
            "Tool-calling contract:",
        ),
        (
            "gpt-4.1-mini",
            "https://api.minimax.io/v1",
            "openai_default",
            "Tool-calling contract:",
        ),
    ],
)
def test_openai_request_compat_characterization(
    model: str,
    base_url: str,
    expected_profile: str,
    expected_label: str,
) -> None:
    profile, rendered, _url, _body = _complete_with_payload(
        model=model, base_url=base_url
    )

    assert profile == expected_profile
    assert expected_label in rendered
    assert (
        "Native tool-calling contract:" not in rendered
        if expected_label == "Tool-calling contract:"
        else "Tool-calling contract:" not in rendered
    )


@pytest.mark.parametrize(
    (
        "preset_id",
        "model",
        "expected_vendor",
        "expected_model_family",
        "expected_url",
    ),
    [
        (
            "minimax",
            "MiniMax-M2.7",
            "minimax",
            "minimax",
            "https://api.minimax.io/v1/chat/completions",
        ),
        (
            "kimi",
            "kimi-k2.6",
            "kimi",
            "kimi",
            "https://api.moonshot.ai/v1/chat/completions",
        ),
        (
            "zai",
            "glm-5.2",
            "zai",
            "glm",
            "https://api.z.ai/api/paas/v4/chat/completions",
        ),
        (
            "zai-coding",
            "glm-5.2",
            "zai-coding",
            "glm",
            "https://api.z.ai/api/coding/paas/v4/chat/completions",
        ),
        (
            "deepseek",
            "deepseek-v4-flash",
            "deepseek",
            "deepseek",
            "https://api.deepseek.com/chat/completions",
        ),
        (
            "qwen-dashscope",
            "qwen3.7-plus",
            "dashscope",
            "qwen",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        ),
        (
            "gemini",
            "gemini-3.6-flash",
            "gemini",
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        ),
        (
            "xai",
            "grok-4.5",
            "xai",
            "grok",
            "https://api.x.ai/v1/chat/completions",
        ),
        (
            "mistral",
            "mistral-large-latest",
            "mistral",
            "mistral",
            "https://api.mistral.ai/v1/chat/completions",
        ),
        (
            "together",
            "MiniMaxAI/MiniMax-M2.7",
            "together",
            "minimax",
            "https://api.together.ai/v1/chat/completions",
        ),
        (
            "cortensor-portal",
            "oss-20b",
            "cortensor",
            "oss",
            "https://api.cortensor.app/v1/chat/completions",
        ),
    ],
)
def test_frontier_openai_compatible_presets_resolve_identity_and_endpoint(
    preset_id: str,
    model: str,
    expected_vendor: str,
    expected_model_family: str,
    expected_url: str,
) -> None:
    preset = get_setup_preset(preset_id)
    identity = resolve_provider_identity_translation(
        preset.runtime_adapter,
        model=model,
        base_url=preset.default_base_url,
    )

    profile = resolve_behavior_profile(
        provider=preset.runtime_adapter,
        model=model,
        base_url=preset.default_base_url,
        provider_identity=identity,
    )
    request_compat, _rendered, url, body = _complete_with_payload(
        model=model,
        base_url=preset.default_base_url,
    )

    assert identity["service_vendor"] == expected_vendor
    assert identity["model_family"] == expected_model_family
    assert profile.provider_identity is not None
    assert profile.provider_identity.service_vendor == expected_vendor
    assert profile.provider_identity.model_family == expected_model_family
    assert url == expected_url
    assert body["model"] == model
    if preset_id == "cortensor-portal":
        assert request_compat == "cortensor_portal"
        assert "tools" in body
        assert body["tool_choice"] == {
            "type": "function",
            "function": {"name": "submit_output"},
        }
        assert "Tool-calling contract:" not in _rendered
        assert "Native tool-calling contract:" not in _rendered
    else:
        assert "tools" in body
        assert body["tool_choice"] == {
            "type": "function",
            "function": {"name": "submit_output"},
        }
    if preset_id == "minimax":
        assert request_compat == "minimax_openai_compat"
    elif preset_id != "cortensor-portal":
        assert request_compat == "openai_default"


def test_cortensor_portal_stream_payload_includes_native_tools() -> None:
    provider = OpenAIProvider()
    request = LLMRequest.model_validate(
        {
            "model": "oss-20b",
            "messages": [{"role": "user", "content": "Count 1, 2, 3."}],
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
    captured: dict[str, object] = {}

    def _fake_stream(**kwargs):
        captured.update(kwargs["payload"])
        yield "data: [DONE]"

    with patch(
        "openminion.modules.llm.providers.openai.adapter.iter_sse_post_lines",
        side_effect=_fake_stream,
    ):
        events = list(
            provider.stream(
                request,
                {
                    "api_key": "fixture-key",
                    "base_url": "https://api.cortensor.app/v1",
                },
            )
        )

    assert [event.type for event in events] == ["done"]
    assert captured["stream"] is True
    assert "tools" in captured
    assert captured["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_output"},
    }


def test_cortensor_portal_preserves_tool_continuation_and_response_facts() -> None:
    provider = OpenAIProvider()
    request = LLMRequest.model_validate(
        {
            "model": "oss-20b",
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-original",
                            "name": "submit_output",
                            "arguments": {"step": 1},
                            "raw_arguments": '{ "step" : 1 }',
                        }
                    ],
                },
                {
                    "role": "tool",
                    "content": '{"ok":true}',
                    "tool_call_id": "call-original",
                },
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
    captured: dict[str, object] = {}

    def _fake_post(**kwargs):
        captured.update(kwargs["payload"])
        kwargs["response_metadata"]["request_id"] = "portal-request-1"
        return {
            "model": "oss-20b",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-returned",
                                "type": "function",
                                "function": {
                                    "name": "submit_output",
                                    "arguments": '{"decision":{"mode":"respond"}}',
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "total_tokens": 5,
            },
        }

    with patch(
        "openminion.modules.llm.providers.openai.adapter._http_json_post",
        side_effect=_fake_post,
    ):
        response = provider.complete(
            request,
            {
                "api_key": "fixture-key",
                "base_url": "https://api.cortensor.app/v1",
                "tool_call_strategy": "hybrid",
            },
        )

    assert captured["messages"] == [
        {
            "role": "system",
            "content": (
                "Schema-only control phase:\n"
                "1. This phase returns structured control output; it must not execute "
                "the user's task.\n"
                "2. Use only the `submit_output` tool. Do not call, describe, or wrap "
                "any other tool.\n"
                "3. Do not emit XML, JSON, markdown, or prose tool envelopes for task tools."
            ),
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-original",
                    "type": "function",
                    "function": {
                        "name": "submit_output",
                        "arguments": '{ "step" : 1 }',
                    },
                }
            ],
        },
        {"role": "tool", "content": '{"ok":true}', "tool_call_id": "call-original"},
    ]
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].id == "call-returned"
    assert response.tool_calls[0].arguments == {"decision": {"mode": "respond"}}
    assert response.tool_calls[0].raw_arguments == '{"decision":{"mode":"respond"}}'
    assert response.telemetry["request_id"] == "portal-request-1"


def test_cortensor_portal_does_not_retry_rejected_tool_payload() -> None:
    provider = OpenAIProvider()
    request = LLMRequest.model_validate(
        {
            "model": "oss-20b",
            "messages": [{"role": "user", "content": "Call the tool."}],
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
    rejected = LLMCtlError(
        "PROVIDER_ERROR",
        "openai request failed with HTTP 400: tool_choice is invalid",
        details={"status_code": 400, "upstream_param": "tool_choice"},
    )

    with (
        patch(
            "openminion.modules.llm.providers.openai.adapter._http_json_post",
            side_effect=rejected,
        ) as post,
        pytest.raises(LLMCtlError),
    ):
        provider.complete(
            request,
            {
                "api_key": "fixture-key",
                "base_url": "https://api.cortensor.app/v1",
                "tool_call_strategy": "hybrid",
            },
        )

    assert post.call_count == 1
    assert post.call_args.kwargs["allow_curl_fallback"] is False


def test_cortensor_portal_stream_preserves_and_reconstructs_tool_deltas() -> None:
    provider = OpenAIProvider()
    request = LLMRequest.model_validate(
        {
            "model": "oss-20b",
            "messages": [{"role": "user", "content": "Call weather."}],
            "tools": [
                {
                    "name": "weather",
                    "description": "look up weather",
                    "input_schema": {"type": "object"},
                }
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": "weather"},
            },
        }
    )
    raw_deltas = [
        {
            "index": 0,
            "id": "call-stream",
            "type": "function",
            "function": {"name": "weather", "arguments": '{"city":"'},
        },
        {"index": 0, "function": {"arguments": 'Paris"}'}},
    ]

    def _fake_stream(**kwargs):
        kwargs["response_metadata"]["request_id"] = "portal-stream-1"
        yield "data: " + json.dumps(
            {"choices": [{"delta": {"tool_calls": [raw_deltas[0]]}}]}
        )
        yield "data: " + json.dumps(
            {"choices": [{"delta": {"tool_calls": [raw_deltas[1]]}}]}
        )
        yield "data: " + json.dumps(
            {
                "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                },
            }
        )
        yield "data: [DONE]"

    with patch(
        "openminion.modules.llm.providers.openai.adapter.iter_sse_post_lines",
        side_effect=_fake_stream,
    ):
        events = list(
            provider.stream(
                request,
                {
                    "api_key": "fixture-key",
                    "base_url": "https://api.cortensor.app/v1",
                    "tool_call_strategy": "hybrid",
                },
            )
        )

    assert [
        event.tool_call_deltas[0] for event in events if event.tool_call_deltas
    ] == raw_deltas
    tool_event = next(event for event in events if event.tool_call is not None)
    assert tool_event.tool_call.id == "call-stream"
    assert tool_event.tool_call.name == "weather"
    assert tool_event.tool_call.arguments == {"city": "Paris"}
    assert tool_event.tool_call.raw_arguments == '{"city":"Paris"}'
    done = events[-1]
    assert done.type == "done"
    assert done.finish_reason == "tool_calls"
    assert done.usage is not None and done.usage.total_tokens == 6
    assert done.request_id == "portal-stream-1"


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_message"),
    [
        ("unsupported", "{}", "unsupported tool"),
        ("weather", "{bad", "malformed arguments"),
        ("weather", "[]", "arguments must be an object"),
    ],
)
def test_cortensor_portal_rejects_invalid_native_tool_responses(
    tool_name: str,
    arguments: str,
    expected_message: str,
) -> None:
    provider = OpenAIProvider()
    request = LLMRequest.model_validate(
        {
            "model": "oss-20b",
            "messages": [{"role": "user", "content": "Call weather."}],
            "tools": [
                {
                    "name": "weather",
                    "description": "look up weather",
                    "input_schema": {"type": "object"},
                }
            ],
            "tool_choice": "required",
        }
    )
    response_payload = {
        "model": "oss-20b",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-invalid",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": arguments,
                            },
                        }
                    ],
                },
            }
        ],
    }

    def _fake_post(**kwargs):
        kwargs["response_metadata"]["request_id"] = "portal-invalid-1"
        return response_payload

    with (
        patch(
            "openminion.modules.llm.providers.openai.adapter._http_json_post",
            side_effect=_fake_post,
        ),
        pytest.raises(LLMCtlError, match=expected_message) as raised,
    ):
        provider.complete(
            request,
            {
                "api_key": "fixture-key",
                "base_url": "https://api.cortensor.app/v1",
            },
        )

    assert raised.value.details["request_id"] == "portal-invalid-1"
    assert raised.value.details["finish_reason"] == "tool_calls"
    if tool_name == "unsupported":
        assert raised.value.details == {
            "tool_name": "unsupported",
            "tool_call_id": "call-invalid",
            "tool_call_index": 0,
            "allowed_tool_names": ["weather"],
            "request_id": "portal-invalid-1",
            "finish_reason": "tool_calls",
        }


def test_cortensor_portal_stream_reports_malformed_tool_arguments() -> None:
    provider = OpenAIProvider()
    request = LLMRequest.model_validate(
        {
            "model": "oss-20b",
            "messages": [{"role": "user", "content": "Call weather."}],
            "tools": [
                {
                    "name": "weather",
                    "description": "look up weather",
                    "input_schema": {"type": "object"},
                }
            ],
            "tool_choice": "required",
        }
    )

    def _fake_stream(**_kwargs):
        yield "data: " + json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-invalid",
                                    "function": {
                                        "name": "weather",
                                        "arguments": "{bad",
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )
        yield "data: [DONE]"

    with patch(
        "openminion.modules.llm.providers.openai.adapter.iter_sse_post_lines",
        side_effect=_fake_stream,
    ):
        events = list(
            provider.stream(
                request,
                {
                    "api_key": "fixture-key",
                    "base_url": "https://api.cortensor.app/v1",
                },
            )
        )

    error = next(event for event in events if event.type == "error")
    assert error.error is not None
    assert "malformed arguments" in error.error.message
    assert error.error.details["tool_call_id"] == "call-invalid"


def test_default_openai_stream_behavior_remains_text_only() -> None:
    provider = OpenAIProvider()
    request = LLMRequest.model_validate(
        {
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "Call weather."}],
            "tools": [
                {
                    "name": "weather",
                    "description": "look up weather",
                    "input_schema": {"type": "object"},
                }
            ],
            "tool_choice": "required",
        }
    )
    captured: dict[str, object] = {}

    def _fake_stream(**kwargs):
        captured.update(kwargs["payload"])
        yield 'data: {"choices":[{"delta":{"content":"hello"}}]}'
        yield (
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
            '"usage":{"total_tokens":2}}'
        )
        yield "data: [DONE]"

    with patch(
        "openminion.modules.llm.providers.openai.adapter.iter_sse_post_lines",
        side_effect=_fake_stream,
    ):
        events = list(provider.stream(request, {"api_key": "fixture-key"}))

    assert "tools" not in captured
    assert "tool_choice" not in captured
    assert [event.type for event in events] == ["delta", "done"]
    assert events[0].delta_text == "hello"
    assert events[-1].finish_reason is None
    assert events[-1].usage is None


def test_cortensor_portal_lists_live_models() -> None:
    provider = OpenAIProvider()

    with patch(
        "openminion.modules.llm.providers.openai.adapter._http_json_get",
        return_value={"data": [{"id": "oss-20b"}, {"id": "oss-120b"}]},
    ) as get:
        models = provider.list_models(
            {
                "api_key": "fixture-key",
                "base_url": "https://api.cortensor.app/v1",
                "provider_identity": {
                    "transport_adapter": "openai_chat",
                    "wire_protocol_family": "openai_chat_completions",
                    "service_vendor": "cortensor",
                    "model_family": "oss",
                },
            }
        )

    assert models == ["oss-20b", "oss-120b"]
    assert get.call_args.kwargs["url"] == "https://api.cortensor.app/v1/models"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "https://example-workspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    ],
)
def test_dashscope_openai_compatible_endpoint_variants_resolve_structurally(
    base_url: str,
) -> None:
    identity = resolve_provider_identity_translation(
        "openai",
        model="qwen-plus",
        base_url=base_url,
    )

    assert identity["service_vendor"] == "dashscope"
    assert identity["model_family"] == "qwen"


@pytest.mark.parametrize(
    ("base_url", "model", "expected_vendor", "expected_family"),
    [
        ("https://api.cortensor.app/v1/", "oss-20b", "cortensor", "oss"),
        ("https://api.cortensor.app:443/v1", "manual-model", "cortensor", "unknown"),
        ("https://api.cortensor.app.evil.test/v1", "oss-20b", "openai", "openai"),
        ("https://api.cortensor.app@evil.test/v1", "oss-20b", "openai", "openai"),
    ],
)
def test_cortensor_portal_identity_uses_exact_parsed_host(
    base_url: str,
    model: str,
    expected_vendor: str,
    expected_family: str,
) -> None:
    identity = resolve_provider_identity_translation(
        "openai",
        model=model,
        base_url=base_url,
    )

    assert identity["service_vendor"] == expected_vendor
    assert identity["model_family"] == expected_family
