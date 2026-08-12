from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
import uuid

import pytest

from openminion.cli.main import main
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService


def _args(tmp_path: Path, *command: str) -> list[str]:
    return [
        "--home-root",
        str(tmp_path / "home"),
        "--data-root",
        str(tmp_path / "data"),
        "status",
        "telemetry",
        *command,
    ]


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "telemetry" / "telemetry.db"


def _record_invocation(
    tmp_path: Path,
    invocation_id: str,
    *,
    terminal: str = "completed",
    timestamp: float = 1.0,
) -> None:
    service = TelemetryService(str(_db_path(tmp_path)))
    try:
        service.record_event_sync(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                event_type="agent.invocation.started",
                event_id=f"start-{invocation_id}",
                timestamp=timestamp,
                invocation_id=invocation_id,
                agent_id="agent-1",
            )
        )
        service.record_event_sync(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                event_type=f"agent.invocation.{terminal}",
                event_id=f"terminal-{invocation_id}",
                timestamp=timestamp + 1,
                invocation_id=invocation_id,
                data={"provider": "provider-1", "model": "model-1"},
            )
        )
    finally:
        service.close_sync()


def _snapshot(path: Path) -> dict[str, tuple[int, int, str]]:
    result = {}
    for candidate in (
        path,
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
        Path(f"{path}-journal"),
    ):
        if candidate.exists() and candidate.is_file():
            stat = candidate.stat()
            result[candidate.name] = (
                stat.st_mode,
                stat.st_mtime_ns,
                hashlib.sha256(candidate.read_bytes()).hexdigest(),
            )
    return result


def test_status_telemetry_text_and_json_share_latest_selection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _record_invocation(tmp_path, "invocation-a", timestamp=1.0)
    _record_invocation(tmp_path, "invocation-b", timestamp=2.0)
    before = _snapshot(_db_path(tmp_path))

    assert main(_args(tmp_path, "--latest", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "openminion.telemetry_debug.v1"
    assert payload["selection"]["selected_invocation_id"] == "invocation-b"

    assert main(_args(tmp_path)) == 0
    text = capsys.readouterr().out
    assert "telemetry: ready" in text
    assert "invocation: invocation-b status=completed" in text
    assert _snapshot(_db_path(tmp_path)) == before


def test_status_telemetry_failed_and_opaque_lookup(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _record_invocation(tmp_path, "invocation-ok")
    _record_invocation(tmp_path, "invocation-1", terminal="failed", timestamp=3.0)

    assert main(_args(tmp_path, "--failed", "--json")) == 0
    failed = json.loads(capsys.readouterr().out)
    assert failed["status"] == "attention"
    assert failed["invocation"]["invocation_id"] == "invocation-1"

    assert main(_args(tmp_path, "--invocation-id", "invocation-1", "--json")) == 0
    explicit = json.loads(capsys.readouterr().out)
    assert explicit["selection"]["source"] == "explicit"


def test_status_telemetry_uuid_equivalence_and_ambiguity(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identifier = uuid.uuid4()
    _record_invocation(tmp_path, str(identifier))

    assert main(_args(tmp_path, "--invocation-id", identifier.hex, "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["invocation"]["invocation_id"] == str(identifier)

    _record_invocation(tmp_path, identifier.hex, timestamp=3.0)
    assert main(_args(tmp_path, "--invocation-id", str(identifier), "--json")) == 3
    ambiguous = json.loads(capsys.readouterr().out)
    assert ambiguous["error"]["code"] == "AMBIGUOUS_INVOCATION_ID"


def test_status_telemetry_empty_not_found_and_invalid_exits_without_creation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parent = _db_path(tmp_path).parent
    parent.mkdir(parents=True)

    assert main(_args(tmp_path, "--json")) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "empty"
    assert main(_args(tmp_path, "--failed", "--json")) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
    assert main(_args(tmp_path, "--invocation-id", "missing", "--json")) == 1
    assert (
        json.loads(capsys.readouterr().out)["error"]["code"] == "INVOCATION_NOT_FOUND"
    )
    assert main(_args(tmp_path, "--invocation-id", "../bad", "--json")) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INVALID_ARGUMENT"
    assert not _db_path(tmp_path).exists()
    assert sorted(path.name for path in parent.iterdir()) == []

    with pytest.raises(SystemExit) as exc_info:
        main(_args(tmp_path, "--latest", "--failed"))
    assert exc_info.value.code == 2


def test_status_telemetry_distinguishes_unavailable_corrupt_and_schema(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(_args(tmp_path, "--json")) == 3
    unavailable = json.loads(capsys.readouterr().out)
    assert unavailable["error"]["code"] == "TELEMETRY_STORAGE_UNAVAILABLE"
    assert not (tmp_path / "data").exists()

    path = _db_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a sqlite database")
    before = _snapshot(path)
    assert main(_args(tmp_path, "--json")) == 3
    corrupt = json.loads(capsys.readouterr().out)
    assert corrupt["error"]["code"] == "TELEMETRY_STORAGE_CORRUPT"
    assert _snapshot(path) == before

    path.unlink()
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    before = _snapshot(path)
    assert main(_args(tmp_path, "--json")) == 3
    incompatible = json.loads(capsys.readouterr().out)
    assert incompatible["error"]["code"] == "TELEMETRY_SCHEMA_INCOMPATIBLE"
    assert _snapshot(path) == before


def test_status_telemetry_unreadable_database_is_normalized(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _record_invocation(tmp_path, "invocation-1")
    path = _db_path(tmp_path)
    real_access = os.access
    monkeypatch.setattr(
        "openminion.modules.telemetry.inspection.os.access",
        lambda candidate, mode: (
            False if Path(candidate) == path else real_access(candidate, mode)
        ),
    )

    assert main(_args(tmp_path, "--json")) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "TELEMETRY_STORAGE_UNAVAILABLE"
