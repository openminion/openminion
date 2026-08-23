from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from openminion.cli.interactive.terminal.shell.actions import _handle_slash
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService


class _Runtime:
    def __init__(self, data_root: Path) -> None:
        self.api_runtime = SimpleNamespace(data_root=data_root)


def _record_invocation(
    data_root: Path,
    invocation_id: str,
    *,
    terminal_event: str = "agent.invocation.failed",
    trace_path: str | None = None,
) -> None:
    service = TelemetryService(
        data_root / "telemetry" / "telemetry.db",
        env={
            "OPENMINION_HOME": str(data_root),
            "OPENMINION_DATA_ROOT": str(data_root),
        },
    )

    async def record() -> None:
        await service.record_event(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                invocation_id=invocation_id,
                agent_id="agent-1",
                event_type="agent.invocation.started",
                timestamp=1.0,
                event_id="start-1",
                data={},
            )
        )
        status = terminal_event.rsplit(".", maxsplit=1)[-1]
        if trace_path:
            await service.record_event(
                TelemetryEvent(
                    session_id="session-1",
                    turn_id="turn-1",
                    invocation_id=invocation_id,
                    agent_id="agent-1",
                    event_type="llm.call.completed",
                    timestamp=1.5,
                    event_id="call-1",
                    data={
                        "llm_call_id": "call-1",
                        "trace_artifact_paths": [trace_path],
                        "trace_artifacts_complete": True,
                    },
                )
            )
        terminal_data = {"status": status}
        if status == "failed":
            terminal_data["error"] = {"type": "TEST_FAILURE"}
        await service.record_event(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                invocation_id=invocation_id,
                agent_id="agent-1",
                event_type=terminal_event,
                timestamp=2.0,
                event_id="failed-1",
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
    assert "status: failed" in _run_slash("/telemetry failed", runtime, tmp_path)


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
        "next: /telemetry failed | /telemetry invocation invocation-completed"
        in output
    )
    assert "shell: telemetryctl debug bundle invocation-completed" in output
    assert "next: telemetryctl" not in output


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
    assert (
        "shell (raw content): telemetryctl trace show "
        f"{relative} --raw" in output
    )


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


def test_terminal_debug_remains_unavailable(tmp_path: Path) -> None:
    output = _run_slash("/debug telemetry", _Runtime(tmp_path), tmp_path)
    assert "not yet implemented" in output
