from __future__ import annotations

import os

import pytest

from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.providers.adapters import OpenAIProvider
from openminion.modules.llm.providers.message_payloads import _http_json_post
from openminion.modules.llm.schemas import LLMRequest

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(600)]

_BASE_URL = "https://api.cortensor.app/v1"
_MODEL = "oss-20b"
_PORTAL_IDENTITY = {
    "transport_adapter": "openai_chat",
    "wire_protocol_family": "openai_chat_completions",
    "service_vendor": "cortensor",
    "model_family": "oss",
}


def _live_config(api_key: str) -> dict[str, object]:
    return {
        "api_key": api_key,
        "base_url": _BASE_URL,
        "model": _MODEL,
        "provider_identity": dict(_PORTAL_IDENTITY),
        "timeout_seconds": 480,
        "tool_call_strategy": "native",
        "http_connection_reuse_enabled": True,
    }


def _require_live_portal() -> str:
    if os.getenv("OPENMINION_LIVE_CORTENSOR_PORTAL_E2E", "").strip() != "1":
        pytest.skip("OPENMINION_LIVE_CORTENSOR_PORTAL_E2E=1 is not set")
    api_key = os.getenv("CORTENSOR_API_KEY", "").strip()
    if not api_key:
        pytest.skip("CORTENSOR_API_KEY is not set in the test process")
    return api_key


def test_live_cortensor_portal_acceptance_runs_serially() -> None:
    api_key = _require_live_portal()
    config = _live_config(api_key)
    provider = OpenAIProvider()
    try:
        assert _MODEL in provider.list_models(config)

        unary = provider.complete(
            LLMRequest(
                model=_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": "Reply with exactly: portal text ok",
                    }
                ],
                temperature=0,
                max_output_tokens=128,
            ),
            config,
        )
        assert unary.output_text.strip() == "portal text ok"
        assert unary.finish_reason
        assert unary.telemetry.get("request_id")

        text_events = list(
            provider.stream(
                LLMRequest(
                    model=_MODEL,
                    messages=[
                        {
                            "role": "user",
                            "content": "Reply with exactly: portal stream ok",
                        }
                    ],
                    temperature=0,
                    max_output_tokens=128,
                    stream=True,
                ),
                config,
            )
        )
        assert "".join(event.delta_text or "" for event in text_events).strip() == (
            "portal stream ok"
        )
        assert text_events[-1].type == "done"
        assert text_events[-1].request_id

        weather_tool = {
            "name": "get_weather",
            "description": "Get weather for one city.",
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
                "additionalProperties": False,
            },
        }
        forced_request = LLMRequest.model_validate(
            {
                "model": _MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": "Call get_weather for Paris.",
                    }
                ],
                "tools": [weather_tool],
                "tool_choice": "required",
                "temperature": 0,
                "max_output_tokens": 256,
            }
        )
        forced = provider.complete(forced_request, config)
        assert forced.finish_reason == "tool_calls"
        assert len(forced.tool_calls) == 1
        tool_call = forced.tool_calls[0]
        assert tool_call.id
        assert tool_call.name == "get_weather"
        assert str(tool_call.arguments.get("city", "")).lower() == "paris"

        continuation = provider.complete(
            LLMRequest.model_validate(
                {
                    "model": _MODEL,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Call get_weather for Paris.",
                        },
                        {
                            "role": "assistant",
                            "tool_calls": [tool_call.model_dump()],
                        },
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": (
                                '{"temperature_c":21,'
                                '"instruction":"Reply exactly: portal continuation ok"}'
                            ),
                        },
                    ],
                    "tools": [weather_tool],
                    "tool_choice": "none",
                    "temperature": 0,
                    "max_output_tokens": 128,
                }
            ),
            config,
        )
        assert continuation.output_text.strip() == "portal continuation ok"
        assert continuation.finish_reason

        tool_events = list(
            provider.stream(
                forced_request.model_copy(update={"stream": True}),
                config,
            )
        )
        streamed_call = next(
            event.tool_call for event in tool_events if event.tool_call is not None
        )
        assert streamed_call.id
        assert streamed_call.name == "get_weather"
        assert str(streamed_call.arguments.get("city", "")).lower() == "paris"
        assert any(event.tool_call_deltas for event in tool_events)
        assert tool_events[-1].finish_reason == "tool_calls"

        with pytest.raises(LLMCtlError) as rejected:
            _http_json_post(
                url=f"{_BASE_URL}/chat/completions",
                payload={
                    "model": _MODEL,
                    "messages": [{"role": "user", "content": "invalid tool"}],
                    "tools": {"type": "unsupported"},
                    "tool_choice": "required",
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout_seconds=480,
                provider_name="openai",
            )
        rejection_details = rejected.value.details
        assert rejection_details.get("status_code") == 422
        # Portal currently omits X-Request-ID from FastAPI validation responses.
    finally:
        provider.close()
