from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openminion.modules.telemetry import cli
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService


def _seed(data_root: Path) -> Path:
    db_path = data_root / "telemetry" / "telemetry.db"
    service = TelemetryService(db_path)

    async def record() -> None:
        await service.record_event(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                invocation_id="invocation-1",
                event_id="start-1",
                event_type="agent.invocation.started",
                timestamp=1.0,
                data={},
            )
        )
        for index, latency in enumerate((10, 20, 30), start=1):
            await service.record_event(
                TelemetryEvent(
                    session_id="session-1",
                    turn_id="turn-1",
                    invocation_id="invocation-1",
                    event_id=f"timing-{index}",
                    event_type="chat.phase_timing",
                    timestamp=1.0 + index,
                    data={
                        "phases_instrumented": ["provider_round_trip"],
                        "provider_round_trip_ms": latency,
                        "provider_attempts": [
                            {
                                "provider": "test-provider",
                                "model": "test-model",
                                "latency_ms": latency + 1,
                            }
                        ],
                    },
                )
            )
        await service.close()

    asyncio.run(record())
    return db_path


def _run(data_root: Path, db_path: Path, args: list[str], capsys) -> tuple[int, dict]:
    exit_code = cli.main(
        [
            "--data-root",
            str(data_root),
            "report",
            "timing",
            *args,
            "--db",
            str(db_path),
        ]
    )
    return exit_code, json.loads(capsys.readouterr().out)


def test_timing_report_uses_nearest_rank_and_direct_provider_attempts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    db_path = _seed(data_root)

    exit_code, payload = _run(data_root, db_path, [], capsys)

    assert exit_code == 0
    assert payload["schema_version"] == "openminion.telemetry_timing_report.v1"
    assert payload["event_count"] == 3
    assert payload["phases"] == [
        {
            "phase": "provider_round_trip",
            "sample_count": 3,
            "p50_ms": 20,
            "p95_ms": 30,
            "max_ms": 30,
        }
    ]
    assert payload["provider_models"] == [
        {
            "provider": "test-provider",
            "model": "test-model",
            "sample_count": 3,
            "p50_ms": 21,
            "p95_ms": 31,
            "max_ms": 31,
        }
    ]


def test_timing_report_excludes_ambiguous_turn_pair(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    db_path = _seed(data_root)
    service = TelemetryService(db_path)
    asyncio.run(
        service.record_event(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                invocation_id="invocation-other",
                event_id="other-event",
                event_type="agent.execution.started",
                timestamp=5.0,
                data={},
            )
        )
    )
    service.close_sync()

    exit_code, payload = _run(data_root, db_path, [], capsys)

    assert exit_code == 0
    assert payload["event_count"] == 0
    codes = {item["code"] for item in payload["diagnostics"]}
    assert codes == {"AMBIGUOUS_TURN_CORRELATION", "NO_PHASE_TIMING_FACTS"}


def test_timing_report_rejects_format_and_bad_bounds(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    db_path = _seed(data_root)

    for args in (["--format", "json"], ["--recent", "0"], ["--limit", "1"]):
        exit_code, payload = _run(data_root, db_path, list(args), capsys)
        assert exit_code == 2
        assert payload["error"]["code"] == "INVALID_ARGUMENT"
