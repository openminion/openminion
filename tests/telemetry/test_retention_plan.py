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
        for index, (invocation_id, terminal_at) in enumerate(
            (("invocation-1", 100.0), ("invocation-2", 200.0)), start=1
        ):
            await service.record_event(
                TelemetryEvent(
                    session_id=f"session-{index}",
                    turn_id=f"turn-{index}",
                    invocation_id=invocation_id,
                    event_id=f"start-{index}",
                    event_type="agent.invocation.started",
                    timestamp=terminal_at - 1,
                    data={},
                )
            )
            await service.record_event(
                TelemetryEvent(
                    session_id=f"session-{index}",
                    turn_id=f"turn-{index}",
                    invocation_id=invocation_id,
                    event_id=f"terminal-{index}",
                    event_type="agent.invocation.completed",
                    timestamp=terminal_at,
                    data={"status": "completed"},
                )
            )
        await service.record_event(
            TelemetryEvent(
                session_id="active-session",
                turn_id="active-turn",
                invocation_id="invocation-active",
                event_id="active-start",
                event_type="agent.invocation.started",
                timestamp=300.0,
                data={},
            )
        )
        await service.close()

    asyncio.run(record())
    return db_path


def _run(
    data_root: Path,
    db_path: Path,
    args: list[str],
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, dict]:
    exit_code = cli.main(
        [
            "--data-root",
            str(data_root),
            "retention",
            "plan",
            *args,
            "--db",
            str(db_path),
        ]
    )
    return exit_code, json.loads(capsys.readouterr().out)


def test_keep_last_plan_is_deterministic_and_read_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    db_path = _seed(data_root)
    before = {path.name: path.stat().st_mtime_ns for path in db_path.parent.iterdir()}

    exit_code, payload = _run(data_root, db_path, ["--keep-last", "1"], capsys)

    after = {path.name: path.stat().st_mtime_ns for path in db_path.parent.iterdir()}
    assert exit_code == 0
    assert payload["schema_version"] == "openminion.telemetry_retention_plan.v1"
    assert payload["selector"] == {
        "kind": "keep_last",
        "older_than_seconds": None,
        "keep_last": 1,
    }
    assert [row["invocation_id"] for row in payload["candidates"]] == ["invocation-1"]
    reasons = {
        row["invocation_id"]: row["reason_codes"] for row in payload["exclusions"]
    }
    assert reasons["invocation-2"] == ["KEEP_LAST_PROTECTED"]
    assert reasons["invocation-active"] == ["ACTIVE_INVOCATION"]
    assert payload["apply_supported"] is False
    assert payload["apply_blocker"] == "cross_store_retention_fence_unavailable"
    assert before == after


def test_older_than_uses_inclusive_cutoff(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    db_path = _seed(data_root)
    monkeypatch.setattr(
        "openminion.modules.telemetry.retention.time.time", lambda: 160.0
    )

    exit_code, payload = _run(data_root, db_path, ["--older-than", "1m"], capsys)

    assert exit_code == 0
    assert [row["invocation_id"] for row in payload["candidates"]] == ["invocation-1"]
    assert payload["candidates"][0]["terminal_epoch_hex"] == (100.0).hex()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--older-than", "30"],
        ["--keep-last", "0"],
        ["--older-than", "1d", "--keep-last", "1"],
        ["--keep-last", "1", "--apply"],
        ["--keep-last", "1", "--force"],
        ["--keep-last", "1", "--plan-hash", "value"],
    ],
)
def test_retention_rejects_invalid_or_mutating_grammar(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    db_path = _seed(data_root)

    exit_code, payload = _run(data_root, db_path, args, capsys)

    assert exit_code == 2
    assert payload["status"] == "error"
    assert payload["selector"] is None
    assert payload["error"] == {
        "code": "INVALID_ARGUMENT",
        "category": "argument",
    }
    assert payload["apply_supported"] is False


def test_missing_store_is_empty_and_creates_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root = tmp_path / "data"
    db_path = data_root / "missing" / "telemetry.db"
    db_path.parent.mkdir(parents=True)

    exit_code, payload = _run(data_root, db_path, ["--keep-last", "2"], capsys)

    assert exit_code == 0
    assert payload["status"] == "empty"
    assert payload["high_water_storage_sequence"] is None
    assert payload["candidates"] == []
    assert not db_path.exists()
