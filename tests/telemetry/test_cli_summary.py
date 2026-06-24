from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from openminion.modules.telemetry.cli import main
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService


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
