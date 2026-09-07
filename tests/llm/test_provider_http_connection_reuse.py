from __future__ import annotations

import json

import httpx
import pytest

from openminion.base.config import OpenMinionConfig
from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.providers.transport.client import ProviderHTTPClient
from openminion.modules.llm.providers.transport.http import http_json_get
from openminion.modules.llm.providers.transport.sse import iter_sse_post_lines


class _RecordingTelemetry:
    def __init__(self) -> None:
        self.operations: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.counters: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def emit_module_operation(self, *args: object, **kwargs: object) -> None:
        self.operations.append((args, kwargs))

    def emit_module_counter(self, *args: object, **kwargs: object) -> None:
        self.counters.append((args, kwargs))


def _client_with_transport(handler) -> tuple[ProviderHTTPClient, httpx.Client]:
    client = ProviderHTTPClient()
    httpx_client = httpx.Client(transport=httpx.MockTransport(handler))
    client._client = httpx_client
    return client, httpx_client


def test_provider_http_client_reuses_one_client_for_json_requests(monkeypatch) -> None:
    requests: list[httpx.Request] = []
    client_configs: list[dict[str, object]] = []
    httpx_client_type = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    def build_client(**kwargs: object) -> httpx.Client:
        client_configs.append(kwargs)
        return httpx_client_type(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(
        "openminion.modules.llm.providers.transport.client.httpx.Client",
        build_client,
    )
    client = ProviderHTTPClient()
    try:
        for _ in range(2):
            assert http_json_get(
                url="https://provider.example/models",
                headers={},
                timeout_seconds=1,
                provider_name="provider",
                http_client=client,
            ) == {"ok": True}
    finally:
        httpx_client = client._client
        client.close()

    assert client_configs == [{"follow_redirects": True, "trust_env": True}]
    assert len(requests) == 2
    assert httpx_client is not None and httpx_client.is_closed


def test_provider_http_client_captures_success_request_id() -> None:
    client, _ = _client_with_transport(
        lambda _request: httpx.Response(
            200,
            json={"ok": True},
            headers={"X-Request-ID": "request-json-1"},
        )
    )
    response_metadata: dict[str, str] = {"stale": "value"}
    try:
        payload = http_json_get(
            url="https://provider.example/models",
            headers={},
            timeout_seconds=1,
            provider_name="provider",
            http_client=client,
            response_metadata=response_metadata,
        )
    finally:
        client.close()

    assert payload == {"ok": True}
    assert response_metadata == {"request_id": "request-json-1"}


def test_provider_http_client_preserves_http_error_mapping() -> None:
    client, _ = _client_with_transport(
        lambda _request: httpx.Response(401, json={"error": "bad key"})
    )
    try:
        with pytest.raises(LLMCtlError) as exc_info:
            http_json_get(
                url="https://provider.example/models",
                headers={},
                timeout_seconds=1,
                provider_name="provider",
                http_client=client,
            )
    finally:
        client.close()

    assert exc_info.value.code == "AUTH_ERROR"
    assert exc_info.value.details["status_code"] == 401


def test_provider_http_client_preserves_timeout_mapping() -> None:
    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client, _ = _client_with_transport(timeout)
    try:
        with pytest.raises(LLMCtlError) as exc_info:
            http_json_get(
                url="https://provider.example/models",
                headers={},
                timeout_seconds=1,
                provider_name="provider",
                http_client=client,
            )
    finally:
        client.close()

    assert exc_info.value.code == "TIMEOUT"


def test_provider_http_client_streams_sse_lines() -> None:
    client, _ = _client_with_transport(
        lambda _request: httpx.Response(
            200,
            content=b"data: first\n\ndata: [DONE]\n\n",
        )
    )
    try:
        lines = list(
            iter_sse_post_lines(
                url="https://provider.example/stream",
                payload={"stream": True},
                headers={},
                timeout_seconds=1,
                provider_name="provider",
                http_client=client,
            )
        )
    finally:
        client.close()

    assert lines == ["data: first", "", "data: [DONE]", ""]


def test_provider_http_client_captures_stream_request_id() -> None:
    client, _ = _client_with_transport(
        lambda _request: httpx.Response(
            200,
            content=b"data: [DONE]\n\n",
            headers={"X-Request-ID": "request-stream-1"},
        )
    )
    response_metadata: dict[str, str] = {"stale": "value"}
    try:
        lines = list(
            iter_sse_post_lines(
                url="https://provider.example/stream",
                payload={"stream": True},
                headers={},
                timeout_seconds=1,
                provider_name="provider",
                http_client=client,
                response_metadata=response_metadata,
            )
        )
    finally:
        client.close()

    assert lines == ["data: [DONE]", ""]
    assert response_metadata == {"request_id": "request-stream-1"}


def test_sse_trace_records_consumed_lines_and_normal_completion(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS_DIR", str(tmp_path))
    telemetry = _RecordingTelemetry()
    client, _ = _client_with_transport(
        lambda _request: httpx.Response(
            200,
            content=b"data: first\n\ndata: [DONE]\n",
            headers={"X-Request-ID": "request-stream-1"},
        )
    )
    try:
        lines = list(
            iter_sse_post_lines(
                url="https://user:secret@provider.example/stream?token=secret#part",
                payload={"stream": True},
                headers={},
                timeout_seconds=1,
                provider_name="provider",
                http_client=client,
                trace_metadata={
                    "session_id": "sess",
                    "turn_id": "turn",
                    "invocation_id": "invocation-1",
                    "inference_step": 1,
                    "trace_label": "call01",
                },
                telemetryctl=telemetry,
            )
        )
    finally:
        client.close()

    trace_path = (
        tmp_path / "llm" / "sess" / "turn-sess" / "step01-call01-http-sse-response.json"
    )
    traced = json.loads(trace_path.read_text(encoding="utf-8"))
    assert traced["lines"] == lines
    assert traced["complete"] is True
    assert traced["request_id"] == "request-stream-1"
    assert traced["url"] == (
        "https://<redacted>:<redacted>@provider.example/stream?token=<redacted>"
    )
    assert traced["error"] == {}
    assert len(telemetry.operations) == 1
    operation_args, operation_kwargs = telemetry.operations[0]
    assert operation_args[:2] == ("sess", "turn")
    assert operation_kwargs["status"] == "ok"
    operation_extra = operation_kwargs["extra"]
    assert isinstance(operation_extra, dict)
    assert operation_extra["transport"] == "httpx_pool"
    assert operation_extra["first_event_ms"] >= 0
    assert operation_extra["invocation_id"] == "invocation-1"


def test_sse_trace_records_consumer_close_without_provider_error(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS_DIR", str(tmp_path))
    client, _ = _client_with_transport(
        lambda _request: httpx.Response(200, content=b"data: first\ndata: second\n")
    )
    stream = iter_sse_post_lines(
        url="https://provider.example/stream",
        payload={"stream": True},
        headers={},
        timeout_seconds=1,
        provider_name="provider",
        http_client=client,
        trace_metadata={
            "session_id": "sess",
            "turn_id": "turn",
            "inference_step": 1,
            "trace_label": "call01",
        },
    )
    try:
        assert next(stream) == "data: first"
        stream.close()
    finally:
        client.close()

    trace_path = (
        tmp_path / "llm" / "sess" / "turn-sess" / "step01-call01-http-sse-response.json"
    )
    traced = json.loads(trace_path.read_text(encoding="utf-8"))
    assert traced["lines"] == ["data: first"]
    assert traced["complete"] is False
    assert traced["error"] == {}


def test_sse_trace_records_structural_timeout_and_preserves_error(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS_DIR", str(tmp_path))

    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    telemetry = _RecordingTelemetry()
    client, _ = _client_with_transport(timeout)
    try:
        with pytest.raises(LLMCtlError) as exc_info:
            list(
                iter_sse_post_lines(
                    url="https://provider.example/stream",
                    payload={"stream": True},
                    headers={},
                    timeout_seconds=1,
                    provider_name="provider",
                    http_client=client,
                    trace_metadata={
                        "session_id": "sess",
                        "turn_id": "turn",
                        "inference_step": 1,
                        "trace_label": "call01",
                    },
                    telemetryctl=telemetry,
                )
            )
    finally:
        client.close()

    assert exc_info.value.code == "TIMEOUT"
    trace_path = (
        tmp_path / "llm" / "sess" / "turn-sess" / "step01-call01-http-sse-response.json"
    )
    traced = json.loads(trace_path.read_text(encoding="utf-8"))
    assert traced["complete"] is False
    assert traced["error"] == {"code": "TIMEOUT", "type": "LLMCtlError"}
    assert len(telemetry.operations) == 1
    assert len(telemetry.counters) == 1
    assert telemetry.operations[0][1]["status"] == "error"
    assert telemetry.counters[0][0][:2] == ("sess", "turn")


def test_provider_connection_reuse_config_defaults_on_with_rollback() -> None:
    default_config = OpenMinionConfig.from_dict({})
    rollback_config = OpenMinionConfig.from_dict(
        {
            "providers": {
                "openrouter": {"http_connection_reuse_enabled": False},
            }
        }
    )

    assert default_config.providers.openrouter.http_connection_reuse_enabled is True
    assert rollback_config.providers.openrouter.http_connection_reuse_enabled is False
