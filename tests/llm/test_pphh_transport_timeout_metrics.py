from __future__ import annotations

import socket
from urllib import error as urllib_error

import pytest

from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.providers.transport import http as http_transport


class _RecordingTelemetryCtl:
    def __init__(self) -> None:
        self.counters: list[dict[str, object]] = []

    def emit_module_counter(self, *args, **kwargs) -> None:
        self.counters.append({"args": args, "kwargs": kwargs})


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
