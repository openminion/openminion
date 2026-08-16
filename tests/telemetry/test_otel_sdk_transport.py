from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
from threading import Thread

import pytest

from openminion.base.config import OTELExporterConfig
from openminion.modules.telemetry.export.attributes import attributes_for_event
from openminion.modules.telemetry.export.otel import OpenTelemetryTraceExporter
from openminion.modules.telemetry.export.sdk import (
    _http_signal_endpoint,
    create_otel_trace_sink,
)
from openminion.modules.telemetry.schemas import TelemetryEvent


def test_http_base_endpoint_expands_to_signal_paths() -> None:
    endpoint = "http://collector:4318"

    assert _http_signal_endpoint(endpoint, "traces") == f"{endpoint}/v1/traces"
    assert _http_signal_endpoint(endpoint, "metrics") == f"{endpoint}/v1/metrics"
    assert _http_signal_endpoint(endpoint, "logs") == f"{endpoint}/v1/logs"


def test_http_signal_endpoint_is_rebased_for_each_signal() -> None:
    endpoint = "https://collector.example/tenant/v1/traces?key=value"

    assert _http_signal_endpoint(endpoint, "metrics") == (
        "https://collector.example/tenant/v1/metrics?key=value"
    )


def test_http_export_posts_each_signal_to_its_standard_path() -> None:
    paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            paths.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, _format: str, *args: object) -> None:
            del args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(
            enabled=True,
            endpoint=f"http://127.0.0.1:{server.server_port}",
            protocol="http/protobuf",
            noncritical_queue_capacity=0,
        )
    )

    def event(event_type: str, **data: object) -> TelemetryEvent:
        return TelemetryEvent(
            session_id="session-1",
            turn_id="turn-1",
            event_type=event_type,
            trace_key="trace-1",
            invocation_id="invocation-1",
            execution_id="execution-1",
            agent_id="agent-1",
            data=dict(data),
        )

    try:
        exporter.export(event("agent.execution.started", agent_name="agent-1"))
        exporter.export(event("agent.execution.completed", status="completed"))
        exporter.export(
            event(
                "telemetry.queue.stats",
                queue_depth=0,
                drops=0,
                flush_failures=0,
                flush_latency_ms=1,
            )
        )
        exporter.export(event("telemetry.export.probe", protocol="http/protobuf"))
        exporter.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert {"/v1/traces", "/v1/metrics", "/v1/logs"}.issubset(paths)


def test_unknown_protocol_disables_export(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = OTELExporterConfig(
        enabled=True,
        endpoint="http://collector:4318",
        protocol="unknown",
    )

    with caplog.at_level(logging.WARNING):
        sink = create_otel_trace_sink(config, logger=logging.getLogger(__name__))

    assert sink is None
    assert "Unsupported OpenTelemetry protocol" in caplog.text


def test_failed_agent_event_has_canonical_error_attributes() -> None:
    attributes = attributes_for_event(
        TelemetryEvent(
            session_id="session-1",
            turn_id="turn-1",
            event_type="agent.execution.failed",
            data={"status": "failed", "error": {"type": "runtime_failure"}},
        ),
        include_assistant_body=False,
    )

    assert attributes["openminion.status"] == "failed"
    assert attributes["error.type"] == "runtime_failure"
