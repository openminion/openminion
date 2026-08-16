from __future__ import annotations

from pathlib import Path
import time

import pytest

from openminion.base.config import OTELExporterConfig
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService


def _artifact_contains(path: Path, expected: str) -> bool:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.exists() and expected in path.read_text(encoding="utf-8"):
            return True
        time.sleep(0.25)
    return False


@pytest.mark.e2e
def test_otlp_http_routes_traces_metrics_and_logs(
    collector_artifacts: Path,
    tmp_path: Path,
) -> None:
    invocation_id = "1eeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    execution_id = "2eeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    common = {
        "session_id": "session-http-signals",
        "turn_id": "turn-http-signals",
        "trace_key": invocation_id,
        "invocation_id": invocation_id,
        "execution_id": execution_id,
        "agent_id": "http-agent",
    }
    service = TelemetryService(
        home_root=tmp_path,
        otel_exporter_config=OTELExporterConfig(
            enabled=True,
            endpoint="http://127.0.0.1:14318",
            protocol="http/protobuf",
            service_name="openminion-otlp-http-e2e",
        ),
    )
    for event_type, data in (
        ("agent.execution.started", {"agent_name": "http-agent"}),
        (
            "tool.execution.started",
            {"tool_call_id": "http-tool", "tool_name": "http.lookup"},
        ),
        (
            "tool.execution.failed",
            {
                "tool_call_id": "http-tool",
                "tool_name": "http.lookup",
                "status": "failed",
                "error": {"type": "http_test_failure"},
            },
        ),
        (
            "agent.execution.failed",
            {"status": "failed", "error": {"type": "http_test_failure"}},
        ),
    ):
        service.record_event_sync(
            TelemetryEvent(event_type=event_type, data=data, **common)
        )
    service.close_sync()

    assert _artifact_contains(collector_artifacts / "traces.json", invocation_id)
    assert _artifact_contains(
        collector_artifacts / "metrics.json", "openminion_tool_calls_total"
    )
    assert _artifact_contains(
        collector_artifacts / "logs.json", "tool.execution.failed"
    )
