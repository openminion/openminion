from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openminion.modules.telemetry import cli
from openminion.modules.telemetry.reports import CORRELATION_FIELDS
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
                execution_id="execution-1",
                agent_id="agent-1",
                event_id="start-1",
                event_type="agent.invocation.started",
                timestamp=1.0,
                data={"trace_id": "legacy-trace", "run_id": "run-1"},
            )
        )
        await service.record_event(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                invocation_id="invocation-1",
                execution_id="execution-1",
                event_id="llm-1",
                event_type="llm.call.completed",
                timestamp=2.0,
                data={"llm_call_id": "llm-1"},
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
            "correlation",
            *args,
            "--db",
            str(db_path),
        ]
    )
    return exit_code, json.loads(capsys.readouterr().out)


def test_correlation_report_has_fixed_fields_and_direct_coverage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    db_path = _seed(data_root)

    exit_code, payload = _run(data_root, db_path, [], capsys)

    assert exit_code == 0
    assert payload["schema_version"] == ("openminion.telemetry_correlation_report.v1")
    assert payload["scope"] == {
        "kind": "recent",
        "session_id": None,
        "limit": 20,
    }
    assert [row["field"] for row in payload["fields"]] == list(CORRELATION_FIELDS)
    coverage = {row["field"]: row for row in payload["fields"]}
    assert coverage["trace_key"]["coverage"] == "1.0000"
    assert coverage["tool_call_id"]["coverage"] == "0.0000"
    assert all(row["total"] == payload["invocation_count"] for row in payload["fields"])


def test_correlation_session_scope_and_invalid_grammar(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    db_path = _seed(data_root)

    exit_code, payload = _run(
        data_root,
        db_path,
        ["--session-id", "session-1", "--limit", "1"],
        capsys,
    )
    assert exit_code == 0
    assert payload["invocation_count"] == 1
    assert payload["scope"]["kind"] == "session"

    exit_code, payload = _run(
        data_root,
        db_path,
        ["--session-id", "bad id"],
        capsys,
    )
    assert exit_code == 2
    assert payload["error"]["code"] == "INVALID_ARGUMENT"

    exit_code, payload = _run(
        data_root,
        db_path,
        ["--recent", "1001"],
        capsys,
    )
    assert exit_code == 2
    assert payload["status"] == "error"


def test_correlation_missing_store_is_empty(tmp_path: Path, capsys) -> None:
    data_root = tmp_path / "data"
    db_path = data_root / "missing" / "telemetry.db"
    db_path.parent.mkdir(parents=True)

    exit_code, payload = _run(data_root, db_path, [], capsys)

    assert exit_code == 0
    assert payload["status"] == "empty"
    assert payload["invocation_count"] == 0
    assert all(row["coverage"] is None for row in payload["fields"])
    assert not db_path.exists()
