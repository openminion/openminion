from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from openminion.modules.telemetry.cli import main
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService


def _record(path: Path, invocation_id: str = "invocation-1") -> None:
    service = TelemetryService(str(path))
    try:
        service.record_event_sync(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                event_type="agent.invocation.started",
                event_id=f"start-{invocation_id}",
                timestamp=1.0,
                invocation_id=invocation_id,
            )
        )
    finally:
        service.close_sync()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_installed_and_module_entrypoints_expose_debug_help() -> None:
    root = Path(__file__).resolve().parents[2]
    commands = (
        [str(root / ".venv/bin/telemetryctl"), "debug", "--help"],
        [sys.executable, "-m", "openminion.modules.telemetry", "debug", "--help"],
    )
    for command in commands:
        result = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "{latest,failed,invocation,bundle}" in result.stdout


def test_debug_routes_and_existing_invocation_routes_accept_opaque_ids(
    capsys,
    tmp_path: Path,
) -> None:
    path = tmp_path / ".openminion" / "telemetry.db"
    _record(path)
    before = _digest(path)

    assert main(["debug", "latest", "--db", str(path)]) == 0
    latest = json.loads(capsys.readouterr().out)
    assert latest["invocation"]["invocation_id"] == "invocation-1"
    assert latest["links"]["commands"] == [
        "telemetryctl invocation show invocation-1",
        "telemetryctl invocation graph invocation-1",
        "telemetryctl debug bundle invocation-1",
    ]
    assert main(["debug", "invocation", "invocation-1", "--db", str(path)]) == 0
    explicit = json.loads(capsys.readouterr().out)
    assert explicit["selection"]["source"] == "explicit"
    assert main(["invocation", "show", "invocation-1", "--db", str(path)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["invocation_id"] == "invocation-1"
    assert main(["invocation", "graph", "invocation-1", "--db", str(path)]) == 0
    graph = json.loads(capsys.readouterr().out)
    assert graph["invocation_id"] == "invocation-1"
    assert _digest(path) == before


def test_debug_exit_contract_and_missing_store_no_create(
    capsys, tmp_path: Path
) -> None:
    parent = tmp_path / ".openminion" / "existing"
    parent.mkdir(parents=True)
    path = parent / "telemetry.db"

    assert main(["debug", "latest", "--db", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "empty"
    assert main(["debug", "invocation", "missing", "--db", str(path)]) == 1
    assert (
        json.loads(capsys.readouterr().out)["error"]["code"] == "INVOCATION_NOT_FOUND"
    )
    assert main(["debug", "invocation", "../bad", "--db", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INVALID_ARGUMENT"
    assert not path.exists()
    assert list(parent.iterdir()) == []

    unavailable = tmp_path / ".openminion" / "missing" / "telemetry.db"
    assert main(["debug", "latest", "--db", str(unavailable)]) == 3
    assert (
        json.loads(capsys.readouterr().out)["error"]["code"]
        == "TELEMETRY_STORAGE_UNAVAILABLE"
    )
    assert not unavailable.parent.exists()


def test_debug_rejects_unreviewed_format_flag_in_subprocess(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            str(root / ".venv/bin/telemetryctl"),
            "debug",
            "latest",
            "--format",
            "text",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_installed_debug_subprocess_preserves_all_exit_codes(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    executable = str(root / ".venv/bin/telemetryctl")
    parent = tmp_path / ".openminion" / "existing"
    parent.mkdir(parents=True)
    empty_path = parent / "telemetry.db"
    cases = (
        (["debug", "latest", "--db", str(empty_path)], 0),
        (["debug", "invocation", "missing", "--db", str(empty_path)], 1),
        (["debug", "invocation", "../bad", "--db", str(empty_path)], 2),
        (["debug", "latest", "--db", str(tmp_path / ".openminion/missing/db")], 3),
    )
    for arguments, expected in cases:
        result = subprocess.run(
            [executable, *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == expected
        assert json.loads(result.stdout)["status"] in {"empty", "error"}
