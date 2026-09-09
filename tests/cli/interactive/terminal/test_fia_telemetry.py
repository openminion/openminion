from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from openminion.base.config import OTELExporterConfig, OpenMinionConfig
from openminion.cli.interactive.terminal.shell.actions import _handle_slash
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.export.otel import (
    OpenTelemetryTraceExporter,
    RecordingOTELTraceSink,
)
from openminion.modules.telemetry.service import TelemetryService


@pytest.fixture(autouse=True)
def _bind_runtime_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path))


class _Runtime:
    def __init__(
        self,
        data_root: Path,
        session_id: str = "session-1",
        *,
        exporter_enabled: bool = False,
        trace_requests: bool = False,
    ) -> None:
        config = OpenMinionConfig()
        if exporter_enabled:
            config.runtime.telemetry_exporter = OTELExporterConfig(
                enabled=True,
                endpoint="http://collector.internal:4318",
            )
        if trace_requests:
            config.runtime.env["OPENMINION_TRACE_REQUESTS"] = "1"
        self.api_runtime = SimpleNamespace(data_root=data_root, config=config)
        self.session_id = session_id


def _record_invocation(
    data_root: Path,
    invocation_id: str,
    *,
    terminal_event: str = "agent.invocation.failed",
    trace_path: str | None = None,
    trace_session_id: str | None = None,
    session_id: str = "session-1",
    timestamp: float = 1.0,
) -> None:
    service = TelemetryService(
        data_root / "telemetry" / "telemetry.db",
        include_local_content=True,
        env={
            "OPENMINION_HOME": str(data_root),
            "OPENMINION_DATA_ROOT": str(data_root),
        },
    )

    async def record() -> None:
        await service.record_event(
            TelemetryEvent(
                session_id=session_id,
                turn_id="turn-1",
                invocation_id=invocation_id,
                agent_id="agent-1",
                event_type="agent.invocation.started",
                timestamp=timestamp,
                event_id=f"start-{invocation_id}",
                data={},
            )
        )
        status = terminal_event.rsplit(".", maxsplit=1)[-1]
        if trace_path:
            await service.record_event(
                TelemetryEvent(
                    session_id=trace_session_id or session_id,
                    turn_id="turn-1",
                    invocation_id=invocation_id,
                    agent_id="agent-1",
                    event_type="llm.call.completed",
                    timestamp=timestamp + 0.5,
                    event_id=f"call-{invocation_id}",
                    trace_key=f"trace-{invocation_id}",
                    data={
                        "status": "ok",
                        "operation": "chat",
                        "model": "model-1",
                        "llm_call_id": f"call-{invocation_id}",
                        "provider_round_trip_ms": 12,
                        "trace_artifact_paths": [trace_path],
                        "trace_artifacts_complete": True,
                        "content": "private response",
                    },
                )
            )
        terminal_data = {"status": status}
        if status == "failed":
            terminal_data["error"] = {"type": "TEST_FAILURE"}
        await service.record_event(
            TelemetryEvent(
                session_id=session_id,
                turn_id="turn-1",
                invocation_id=invocation_id,
                agent_id="agent-1",
                event_type=terminal_event,
                timestamp=timestamp + 1.0,
                event_id=f"terminal-{invocation_id}",
                data=terminal_data,
            )
        )
        await service.close()

    asyncio.run(record())


def _run_slash(text: str, runtime: _Runtime, tmp_path: Path) -> str:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=160)
    transcript = TerminalTranscript(console)
    asyncio.run(
        _handle_slash(
            text,
            runtime=runtime,
            console=console,
            transcript=transcript,
            overlay=SimpleNamespace(),
            status_line=TerminalStatusLine(),
            working_dir=str(tmp_path),
        )
    )
    return buffer.getvalue()


def test_terminal_telemetry_supports_opaque_id_and_failed_selector(
    tmp_path: Path,
) -> None:
    _record_invocation(tmp_path, "invocation-1")
    runtime = _Runtime(tmp_path)

    assert "id: invocation-1" in _run_slash(
        "/telemetry invocation invocation-1", runtime, tmp_path
    )
    failed = _run_slash("/telemetry failed", runtime, tmp_path)
    assert "status: failed" in failed
    assert "failure: TEST_FAILURE" in failed


def test_terminal_telemetry_labels_latest_completed_invocation(
    tmp_path: Path,
) -> None:
    _record_invocation(
        tmp_path,
        "invocation-completed",
        terminal_event="agent.invocation.completed",
    )
    output = _run_slash("/telemetry", _Runtime(tmp_path), tmp_path)

    assert output.startswith("latest invocation")
    assert "latest failed invocation" not in output
    assert (
        "next: /telemetry failed | /telemetry invocation invocation-completed" in output
    )
    assert "shell: telemetryctl invocation show invocation-completed" in output
    assert "next: telemetryctl" not in output


def test_terminal_telemetry_uses_loaded_export_config_and_live_queue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path / "different-home"))
    _record_invocation(
        tmp_path,
        "invocation-export-health",
        terminal_event="agent.invocation.completed",
    )
    exporter_config = OTELExporterConfig(
        enabled=True,
        endpoint="http://collector:4318",
        protocol="http/protobuf",
        noncritical_queue_capacity=0,
    )
    exporter = OpenTelemetryTraceExporter(
        exporter_config,
        sink=RecordingOTELTraceSink(),
    )
    service = TelemetryService(
        tmp_path / "telemetry" / "telemetry.db",
        env={"OPENMINION_DATA_ROOT": str(tmp_path)},
        otel_exporter_config=exporter_config,
        external_exporter=exporter,
    )
    runtime = _Runtime(tmp_path)
    runtime.api_runtime.config.runtime.telemetry_exporter = exporter_config
    runtime.api_runtime.telemetry_service = service

    try:
        output = _run_slash("/telemetry", runtime, tmp_path)
    finally:
        service.close_sync()

    assert "external export: ready (http/protobuf)" in output
    assert "export queue: depth=0/0 drops=0 flush_failures=0" in output


def test_terminal_telemetry_states_exact_capture_posture(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _record_invocation(
        tmp_path,
        "invocation-capture-posture",
        terminal_event="agent.invocation.completed",
    )
    monkeypatch.delenv("OPENMINION_TRACE_REQUESTS", raising=False)
    disabled = _run_slash("/telemetry", _Runtime(tmp_path), tmp_path)
    enabled = _run_slash("/telemetry", _Runtime(tmp_path, trace_requests=True), tmp_path)

    assert "Exact payload capture: disabled" in disabled
    assert "export queue: disabled" in disabled
    assert "Exact payload capture: enabled" in enabled


def test_terminal_trace_show_labels_explicit_raw_shell_access(
    tmp_path: Path,
) -> None:
    relative = "llm/agent-1/run-1/step01-call01-structured.json"
    trace_path = tmp_path / "traces" / relative
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text('{"prompt":"hidden"}', encoding="utf-8")
    _record_invocation(tmp_path, "invocation-trace", trace_path=relative)
    runtime = _Runtime(tmp_path)

    _run_slash("/telemetry", runtime, tmp_path)
    output = _run_slash(f"/trace show {relative}", runtime, tmp_path)

    assert "prompt" not in output
    assert "hidden" not in output
    assert f"shell (raw content): telemetryctl trace show {relative} --raw" in output


def test_terminal_telemetry_usage_and_missing_store_do_not_fall_through(
    tmp_path: Path,
) -> None:
    runtime = _Runtime(tmp_path)
    before = set(tmp_path.iterdir())

    output = _run_slash("/telemetry invocation", runtime, tmp_path)
    missing = _run_slash("/trace list --limit 101", runtime, tmp_path)

    assert output.strip().startswith("usage: /telemetry")
    assert missing.strip().startswith("usage: /trace")
    assert "Unknown command" not in output + missing
    assert set(tmp_path.iterdir()) == before


def test_terminal_telemetry_explains_empty_session(tmp_path: Path) -> None:
    (tmp_path / "telemetry").mkdir()
    output = _run_slash(
        "/telemetry",
        _Runtime(tmp_path, trace_requests=True),
        tmp_path,
    )

    assert output.startswith("Telemetry")
    assert "No model runs in this session yet." in output
    assert "external export: disabled" in output
    assert "Exact payload capture: enabled" in output
    assert "send a prompt, then run /telemetry" in output


def test_terminal_telemetry_shows_runtime_export_posture(tmp_path: Path) -> None:
    (tmp_path / "telemetry").mkdir()
    output = _run_slash(
        "/telemetry",
        _Runtime(tmp_path, exporter_enabled=True),
        tmp_path,
    )

    assert "external export: configured (http/protobuf)" in output
    assert "live health unavailable" in output
    assert "Capture setup: restart with OPENMINION_TRACE_REQUESTS=1" in output
    assert "collector.internal" not in output


def test_terminal_telemetry_stays_in_the_active_session(tmp_path: Path) -> None:
    _record_invocation(
        tmp_path,
        "active-invocation",
        session_id="active-session",
        timestamp=1.0,
    )
    _record_invocation(
        tmp_path,
        "foreign-invocation",
        session_id="foreign-session",
        timestamp=10.0,
    )
    runtime = _Runtime(tmp_path, "active-session")

    latest = _run_slash("/telemetry latest", runtime, tmp_path)
    failed = _run_slash("/telemetry failed", runtime, tmp_path)
    explicit = _run_slash(
        "/telemetry invocation foreign-invocation",
        runtime,
        tmp_path,
    )

    assert "id: active-invocation" in latest
    assert "id: active-invocation" in failed
    assert "INVOCATION_NOT_FOUND" in explicit
    assert "foreign-invocation" not in latest + failed


def test_terminal_telemetry_includes_correlated_child_session_traces(
    tmp_path: Path,
) -> None:
    relative = "llm/agent-1/run-1/step01-call01-structured.json"
    trace_path = tmp_path / "traces" / relative
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text("{}", encoding="utf-8")
    _record_invocation(
        tmp_path,
        "invocation-child-trace",
        terminal_event="agent.invocation.completed",
        trace_path=relative,
        trace_session_id="session-1::conv:focus-session-1",
    )
    runtime = _Runtime(tmp_path)

    telemetry = _run_slash("/telemetry", runtime, tmp_path)
    events = _run_slash("/telemetry events", runtime, tmp_path)
    traces = _run_slash("/trace list", runtime, tmp_path)

    assert "trace files: 1" in telemetry
    assert "llm.call.completed" in events
    assert "session-1::conv:focus-session-1" in events
    assert relative in traces


def test_terminal_telemetry_events_are_safe_ordered_and_bounded(tmp_path: Path) -> None:
    relative = "llm/agent-1/run-1/step01-call01-structured.json"
    trace_path = tmp_path / "traces" / relative
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text("{}", encoding="utf-8")
    _record_invocation(tmp_path, "invocation-events", trace_path=relative)
    runtime = _Runtime(tmp_path)

    missing = _run_slash("/telemetry events", runtime, tmp_path)
    _run_slash("/telemetry", runtime, tmp_path)
    default_output = _run_slash("/telemetry events", runtime, tmp_path)
    output = _run_slash("/telemetry events --limit 2", runtime, tmp_path)
    invalid = _run_slash("/telemetry events --limit 101", runtime, tmp_path)

    assert "NO_SELECTED_INVOCATION" in missing
    assert "telemetry events: invocation-events (3)" in default_output
    assert "telemetry events: invocation-events (2)" in output
    assert output.index("llm.call.completed") < output.index("agent.invocation.failed")
    assert "provider_round_trip_ms" in output
    assert "private response" not in output
    assert invalid.strip().startswith("usage: /telemetry")


def test_terminal_telemetry_requires_an_active_session(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path, "")

    output = _run_slash("/telemetry", runtime, tmp_path)

    assert "ACTIVE_SESSION_UNAVAILABLE" in output


def test_terminal_debug_is_not_available(tmp_path: Path) -> None:
    output = _run_slash("/debug telemetry", _Runtime(tmp_path), tmp_path)
    assert output == "Unknown command: /debug\nType / to view available commands.\n"
