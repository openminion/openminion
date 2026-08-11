from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.base.config import OTELExporterConfig
from openminion.modules.telemetry import cli
from openminion.modules.telemetry.interfaces import TelemetryExportProbeResult


@pytest.mark.parametrize(
    ("config", "diagnostic"),
    [
        (OTELExporterConfig(enabled=False), "EXPORT_DISABLED"),
        (OTELExporterConfig(enabled=True), "EXPORT_ENDPOINT_MISSING"),
        (
            OTELExporterConfig(
                enabled=True,
                endpoint="http://collector",
                protocol="unknown",
            ),
            "UNKNOWN_EXPORT_PROTOCOL",
        ),
    ],
)
def test_live_export_preflight_does_not_open_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    config: OTELExporterConfig,
    diagnostic: str,
) -> None:
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda **_kwargs: SimpleNamespace(otel_exporter=config),
    )
    monkeypatch.setattr(
        cli,
        "TelemetryService",
        lambda *_args, **_kwargs: pytest.fail("service opened during preflight"),
    )

    assert cli.main(["--data-root", str(tmp_path), "doctor", "--live-export"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "openminion.telemetry_export_smoke.v1"
    assert payload["status"] == "incomplete"
    assert payload["probe"] == {
        "cleanup": "not_run",
        "event_id": None,
        "flush": "not_run",
        "local_recording": "not_run",
        "queue": "not_run",
        "sampling": "not_run",
        "timeout_ms": 5000,
        "transport": "skipped",
    }
    assert payload["diagnostics"][0]["code"] == diagnostic
    assert list(tmp_path.iterdir()) == []


def test_live_export_records_once_and_reports_direct_flush(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = OTELExporterConfig(
        enabled=True,
        endpoint="http://collector",
        protocol="http",
    )
    observed: list[object] = []

    class FakeService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def record_and_probe_export(self, event, timeout_seconds):
            observed.extend((event, timeout_seconds))
            return TelemetryExportProbeResult(
                True,
                "accepted",
                "completed",
                recording_sink=True,
            )

        def close_sync(self) -> None:
            return

    monkeypatch.setattr(
        cli,
        "load_config",
        lambda **_kwargs: SimpleNamespace(otel_exporter=config),
    )
    monkeypatch.setattr(cli, "TelemetryService", FakeService)

    assert cli.main(["--data-root", str(tmp_path), "doctor", "--live-export"]) == 0

    payload = json.loads(capsys.readouterr().out)
    event = observed[0]
    assert event.event_type == "telemetry.export.probe"
    assert event.session_id == "telemetry-doctor"
    assert event.turn_id == "live-export-probe"
    assert event.data == {
        "probe_kind": "live_export",
        "criticality": "diagnostic",
        "protocol": "http/protobuf",
    }
    assert observed[1] == 5.0
    assert payload["status"] == "ready"
    assert payload["probe"]["queue"] == "bypassed"
    assert payload["proof"] == {
        "collector_artifact": False,
        "otlp_transport": True,
        "recording_sink": True,
        "vendor_visibility": False,
    }
