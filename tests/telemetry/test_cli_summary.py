from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from openminion.modules.telemetry.cli import main
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService
from openminion.modules.telemetry.cli import _invocation_summary


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_telemetryctl_summary_prints_sorted_module_and_metric_keys(
    capsys, tmp_path: Path
) -> None:
    db_path = tmp_path / ".openminion" / "telemetry.db"
    service = TelemetryService(str(db_path))
    ctl = TelemetryCtl(service)
    _run(
        ctl.emit_module_operation(
            "sess-cli",
            "turn-1",
            "openminion-tool",
            "completed",
            extra={"tool": "echo"},
        )
    )
    _run(
        ctl.emit_module_counter(
            "sess-cli",
            "turn-1",
            "openminion-tool",
            "latency_bucket_ms",
            20.0,
        )
    )
    _run(
        ctl.emit_module_operation(
            "sess-cli",
            "turn-1",
            "openminion-brain",
            "llm_pack",
        )
    )
    _run(service.close())

    assert main(["summary", "--db", str(db_path), "sess-cli"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert list(payload.keys()) == ["openminion-brain", "openminion-tool"]
    assert list(payload["openminion-tool"]["custom_counter_sums"].keys()) == [
        "latency_bucket_ms"
    ]
    assert list(payload["openminion-tool"]["operation_counts"].keys()) == ["completed"]


def test_invocation_summary_reports_legacy_orphan_and_propagation_diagnostics() -> None:
    from openminion.modules.telemetry.schemas import TelemetryEvent

    payload = _invocation_summary(
        "invocation-1",
        [
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                event_type="agent.execution.failed",
                data={"trace_context_status": "invalid", "status": "error"},
            )
        ],
    )

    assert payload["diagnostics"]["orphan_terminal_events"] == 1
    assert payload["diagnostics"]["propagation"]["invalid"] == 1
