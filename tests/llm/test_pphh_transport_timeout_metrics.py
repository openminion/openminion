from __future__ import annotations

import json
import socket
from urllib import error as urllib_error

import pytest

from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.providers.transport import http as http_transport


class _RecordingTelemetryCtl:
    def __init__(self) -> None:
        self.counters: list[dict[str, object]] = []
        self.operations: list[dict[str, object]] = []

    def emit_module_counter(self, *args, **kwargs) -> None:
        self.counters.append({"args": args, "kwargs": kwargs})

    def emit_module_operation(self, *args, **kwargs) -> None:
        self.operations.append({"args": args, "kwargs": kwargs})


class _FakeHTTPResponse:
    status = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _single_operation(
    telemetry: _RecordingTelemetryCtl,
) -> tuple[dict[str, object], dict[str, object]]:
    assert len(telemetry.operations) == 1
    operation = telemetry.operations[0]
    kwargs = operation["kwargs"]
    assert isinstance(kwargs, dict)
    extra = kwargs["extra"]
    assert isinstance(extra, dict)
    return operation, extra


def test_http_json_post_timeout_emits_transport_counter(monkeypatch) -> None:
    telemetry = _RecordingTelemetryCtl()

    def _raise_timeout(*args, **kwargs):
        del args, kwargs
        raise urllib_error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr(http_transport.urllib_request, "urlopen", _raise_timeout)

    with pytest.raises(LLMCtlError) as excinfo:
        http_transport.http_json_post(
            url="https://provider.example/v1",
            payload={"messages": []},
            headers={},
            timeout_seconds=1,
            provider_name="provider",
            telemetryctl=telemetry,
        )

    assert excinfo.value.code == "TIMEOUT"
    assert len(telemetry.counters) == 1
    assert telemetry.counters[0]["args"][3] == "llm_transport_timeout"
    assert telemetry.counters[0]["kwargs"]["extra"]["method"] == "POST"
    operation, extra = _single_operation(telemetry)
    assert operation["args"][3] == "http_json_post"
    assert operation["kwargs"]["status"] == "error"
    assert extra["provider_round_trip_ms"] is None
    assert extra["method"] == "POST"


def test_http_json_get_timeout_emits_transport_counter(monkeypatch) -> None:
    telemetry = _RecordingTelemetryCtl()

    def _raise_timeout(*args, **kwargs):
        del args, kwargs
        raise urllib_error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr(http_transport.urllib_request, "urlopen", _raise_timeout)

    with pytest.raises(LLMCtlError) as excinfo:
        http_transport.http_json_get(
            url="https://provider.example/v1/models",
            headers={},
            timeout_seconds=1,
            provider_name="provider",
            telemetryctl=telemetry,
        )

    assert excinfo.value.code == "TIMEOUT"
    assert len(telemetry.counters) == 1
    assert telemetry.counters[0]["args"][3] == "llm_transport_timeout"
    assert telemetry.counters[0]["kwargs"]["extra"]["method"] == "GET"
    operation, extra = _single_operation(telemetry)
    assert operation["args"][3] == "http_json_get"
    assert operation["kwargs"]["status"] == "error"
    assert extra["method"] == "GET"


def test_http_json_post_success_emits_transport_timing(monkeypatch) -> None:
    telemetry = _RecordingTelemetryCtl()

    def _ok(*args, **kwargs):
        del args, kwargs
        return _FakeHTTPResponse({"ok": True})

    monkeypatch.setattr(http_transport.urllib_request, "urlopen", _ok)

    response = http_transport.http_json_post(
        url="https://provider.example/v1",
        payload={"messages": []},
        headers={},
        timeout_seconds=1,
        provider_name="provider",
        telemetryctl=telemetry,
    )

    assert response == {"ok": True}
    assert telemetry.counters == []
    operation, extra = _single_operation(telemetry)
    assert operation["args"][3] == "http_json_post"
    assert operation["kwargs"]["status"] == "ok"
    assert extra["method"] == "POST"
    assert extra["transport"] == "urllib"
    assert extra["request_bytes"] > 0
    assert extra["response_bytes"] > 0
    assert extra["request_build_ms"] >= 0
    assert extra["provider_round_trip_ms"] >= 0
    assert extra["parse_ms"] >= 0
    assert extra["total_ms"] >= 0
