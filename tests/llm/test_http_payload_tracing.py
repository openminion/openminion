from __future__ import annotations

import json
from io import BytesIO
from urllib import error as urllib_error
from unittest.mock import patch

from openminion.modules.llm.providers.transport.http import http_json_get
from openminion.modules.llm.providers.openai.adapter import OpenAIProvider
from openminion.modules.llm.providers.openrouter.adapter import OpenRouterProvider
from openminion.modules.llm.schemas import LLMRequest


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class _FakeSSEHTTPResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = [line + "\n" for line in lines]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        for line in self._lines:
            yield line.encode("utf-8")


def test_http_payload_trace_file_contains_exact_body(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS_DIR", str(tmp_path))

    provider = OpenAIProvider()
    request = LLMRequest.model_validate(
        {
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {
                "session_id": "sess",
                "turn_id": "turn",
                "inference_step": "1",
                "trace_label": "call01",
            },
        }
    )

    response_payload = {
        "model": "gpt-4.1-mini",
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    sent_body: dict[str, str] = {}
    sent_headers: dict[str, str] = {}

    def _fake_urlopen(request_obj, timeout=None):  # noqa: ARG001
        raw = getattr(request_obj, "data", b"") or b""
        sent_body["json"] = raw.decode("utf-8")
        sent_headers.update(
            {str(key).lower(): str(value) for key, value in request_obj.header_items()}
        )
        return _FakeHTTPResponse(response_payload)

    with patch(
        "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
        side_effect=_fake_urlopen,
    ):
        provider.complete(
            request,
            {
                "api_key": "test-key",
                "base_url": "https://api.openai.com/v1",
                "tool_call_strategy": "off",
            },
        )

    trace_path = tmp_path / "llm" / "sess" / "turn-sess" / "step01-call01-http.json"
    response_trace_path = (
        tmp_path / "llm" / "sess" / "turn-sess" / "step01-call01-http-response.json"
    )
    assert trace_path.exists()
    assert response_trace_path.exists()
    traced = json.loads(trace_path.read_text(encoding="utf-8"))
    response_traced = json.loads(response_trace_path.read_text(encoding="utf-8"))
    assert traced["provider"] == "openai"
    assert traced["method"] == "POST"
    assert traced["url"].endswith("/chat/completions")
    assert traced["json_body"] == sent_body["json"]
    assert traced["json"] == json.loads(sent_body["json"])
    assert traced["json"]["model"] == "gpt-4.1-mini"
    assert traced["headers"]["Authorization"] == "<redacted>"
    assert traced["headers"]["User-Agent"] == "OpenMinion/1.0"
    assert response_traced["provider"] == "openai"
    assert response_traced["status_code"] == 200
    assert response_traced["json"] == response_payload
    assert response_traced["json_parse_error"] == ""
    assert response_traced["lane"]["transport"] == "urllib"
    assert sent_headers["user-agent"] == "OpenMinion/1.0"


def test_http_payload_trace_file_is_emitted_for_streaming(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS_DIR", str(tmp_path))

    provider = OpenRouterProvider()
    request = LLMRequest.model_validate(
        {
            "model": "openai/gpt-4.1-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {
                "session_id": "sess",
                "turn_id": "turn",
                "inference_step": "1",
                "trace_label": "call01",
            },
        }
    )

    sse_lines = [
        'data: {"choices": [{"delta": {"content": "hi"}}]}',
        "data: [DONE]",
    ]
    sent_body: dict[str, str] = {}
    sent_headers: dict[str, str] = {}

    def _fake_urlopen(request_obj, timeout=None):  # noqa: ARG001
        raw = getattr(request_obj, "data", b"") or b""
        sent_body["json"] = raw.decode("utf-8")
        sent_headers.update(
            {str(key).lower(): str(value) for key, value in request_obj.header_items()}
        )
        return _FakeSSEHTTPResponse(sse_lines)

    with patch(
        "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
        side_effect=_fake_urlopen,
    ):
        events = list(
            provider.stream(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://openrouter.ai/api/v1",
                },
            )
        )
    delta_events = [event for event in events if event.type == "delta"]
    assert delta_events
    assert delta_events[0].delta_text == "hi"

    trace_path = tmp_path / "llm" / "sess" / "turn-sess" / "step01-call01-http.json"
    assert trace_path.exists()
    traced = json.loads(trace_path.read_text(encoding="utf-8"))
    assert traced["provider"] == "openrouter"
    assert traced["method"] == "POST"
    assert traced["url"].endswith("/chat/completions")
    assert traced["json_body"] == sent_body["json"]
    assert traced["json"] == json.loads(sent_body["json"])
    assert traced["headers"]["Authorization"] == "<redacted>"
    assert traced["headers"]["User-Agent"] == "OpenMinion/1.0"
    assert sent_headers["user-agent"] == "OpenMinion/1.0"


def test_http_payload_trace_file_does_not_overwrite_when_metadata_missing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS_DIR", str(tmp_path))

    provider = OpenAIProvider()
    request = LLMRequest.model_validate(
        {
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "hello"}],
        }
    )
    response_payload = {
        "model": "gpt-4.1-mini",
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

    with patch(
        "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
        return_value=_FakeHTTPResponse(response_payload),
    ):
        provider.complete(
            request,
            {
                "api_key": "test-key",
                "base_url": "https://api.openai.com/v1",
                "tool_call_strategy": "off",
            },
        )
        provider.complete(
            request,
            {
                "api_key": "test-key",
                "base_url": "https://api.openai.com/v1",
                "tool_call_strategy": "off",
            },
        )

    traces = sorted(
        path
        for path in (tmp_path / "llm" / "agent" / "turn-session").glob(
            "step00-call-http*.json"
        )
        if "-http-response" not in path.name
    )
    assert len(traces) == 2


def test_http_response_trace_file_is_emitted_for_http_error(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS_DIR", str(tmp_path))

    provider = OpenAIProvider()
    request = LLMRequest.model_validate(
        {
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {
                "session_id": "sess",
                "turn_id": "turn",
                "inference_step": "1",
                "trace_label": "call01",
            },
        }
    )

    http_error = urllib_error.HTTPError(
        url="https://api.openai.com/v1/chat/completions",
        code=429,
        msg="Too Many Requests",
        hdrs=None,
        fp=BytesIO(b'{"error":"rate limited"}'),
    )

    with patch(
        "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
        side_effect=http_error,
    ):
        try:
            provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "https://api.openai.com/v1",
                    "tool_call_strategy": "off",
                },
            )
        except Exception:
            pass

    response_trace_path = (
        tmp_path / "llm" / "sess" / "turn-sess" / "step01-call01-http-response.json"
    )
    assert response_trace_path.exists()
    traced = json.loads(response_trace_path.read_text(encoding="utf-8"))
    assert traced["provider"] == "openai"
    assert traced["status_code"] == 429
    assert traced["body_text"] == '{"error":"rate limited"}'
    assert traced["json"] is None
    assert traced["json_parse_error"] == ""
    assert traced["lane"]["status_code"] == 429


def test_http_get_trace_file_records_get_method(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS_DIR", str(tmp_path))

    response_payload = {"data": [{"id": "openai/gpt-4.1-mini"}]}
    sent_headers: dict[str, str] = {}

    def _fake_urlopen(request_obj, timeout=None):  # noqa: ARG001
        sent_headers.update(
            {str(key).lower(): str(value) for key, value in request_obj.header_items()}
        )
        return _FakeHTTPResponse(response_payload)

    with patch(
        "openminion.modules.llm.providers.transport.http.urllib_request.urlopen",
        side_effect=_fake_urlopen,
    ):
        payload = http_json_get(
            url="https://openrouter.ai/api/v1/models",
            headers={
                "Authorization": "Bearer test-key",
                "Content-Type": "application/json",
            },
            timeout_seconds=20,
            provider_name="openrouter",
            trace_metadata={
                "session_id": "sess",
                "turn_id": "turn",
                "inference_step": "1",
                "trace_label": "call01",
            },
        )

    assert payload == response_payload
    trace_path = tmp_path / "llm" / "sess" / "turn-sess" / "step01-call01-http.json"
    response_trace_path = (
        tmp_path / "llm" / "sess" / "turn-sess" / "step01-call01-http-response.json"
    )
    assert trace_path.exists()
    assert response_trace_path.exists()
    traced = json.loads(trace_path.read_text(encoding="utf-8"))
    response_traced = json.loads(response_trace_path.read_text(encoding="utf-8"))
    assert traced["provider"] == "openrouter"
    assert traced["method"] == "GET"
    assert traced["json"] is None
    assert traced["json_body"] == ""
    assert traced["headers"]["Authorization"] == "<redacted>"
    assert response_traced["status_code"] == 200
    assert response_traced["json"] == response_payload
    assert sent_headers["user-agent"] == "OpenMinion/1.0"
