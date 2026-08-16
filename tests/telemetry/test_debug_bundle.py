from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import stat

import pytest

from openminion.modules.telemetry import cli
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService


def _seed(data_root: Path, invocation_id: str = "invocation-1") -> Path:
    db_path = data_root / "telemetry" / "telemetry.db"
    service = TelemetryService(db_path)

    async def record() -> None:
        for event in (
            TelemetryEvent(
                session_id="private-session",
                turn_id="private-turn",
                invocation_id=invocation_id,
                execution_id="private-execution",
                agent_id="private-agent",
                event_id="private-start",
                event_type="agent.invocation.started",
                timestamp=1.0,
                data={"content": "never bundle this"},
            ),
            TelemetryEvent(
                session_id="private-session",
                turn_id="private-turn",
                invocation_id=invocation_id,
                execution_id="private-execution",
                agent_id="private-agent",
                event_id="private-terminal",
                event_type="agent.invocation.failed",
                timestamp=2.0,
                data={
                    "status": "failed",
                    "error": {
                        "type": "TEST_FAILURE",
                        "message": "private free-form failure",
                    },
                },
            ),
        ):
            await service.record_event(event)
        await service.close()

    asyncio.run(record())
    return db_path


def test_bundle_cli_writes_private_atomic_sanitized_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    db_path = _seed(data_root)

    assert (
        cli.main(
            [
                "--data-root",
                str(data_root),
                "debug",
                "bundle",
                "invocation-1",
                "--db",
                str(db_path),
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == ("openminion.telemetry_debug_bundle_result.v1")
    destination = data_root / payload["bundle"]["destination_relative"]
    assert destination.is_dir()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["schema_version"] == "openminion.telemetry_debug_bundle.v1"
    assert manifest["complete"] is True
    assert manifest["files"] == sorted(manifest["files"], key=lambda item: item["path"])
    for item in manifest["files"]:
        path = destination / item["path"]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        assert path.stat().st_size == item["size_bytes"]
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in destination.iterdir()
        if path.is_file()
    )
    for excluded in (
        "private-session",
        "private-turn",
        "private-execution",
        "private-agent",
        "never bundle this",
        "private free-form failure",
        str(tmp_path),
    ):
        assert excluded not in combined
    assert "id-001" in combined


@pytest.mark.parametrize(
    ("invocation_id", "output", "exit_code", "error_code"),
    [
        ("missing", None, 1, "INVOCATION_NOT_FOUND"),
        ("bad id", None, 2, "INVALID_ARGUMENT"),
        ("invocation-1", "../outside", 2, "INVALID_ARGUMENT"),
    ],
)
def test_bundle_cli_returns_typed_errors_without_absolute_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    invocation_id: str,
    output: str | None,
    exit_code: int,
    error_code: str,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    db_path = _seed(data_root)
    argv = [
        "--data-root",
        str(data_root),
        "debug",
        "bundle",
        invocation_id,
        "--db",
        str(db_path),
    ]
    if output is not None:
        argv.extend(("--output", output))

    assert cli.main(argv) == exit_code

    payload = json.loads(capsys.readouterr().out)
    assert payload["bundle"] is None
    assert payload["error"]["code"] == error_code
    assert str(tmp_path) not in json.dumps(payload)


def test_bundle_refuses_existing_and_symlinked_destinations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    db_path = _seed(data_root)
    existing = data_root / "existing"
    existing.mkdir()

    argv = [
        "--data-root",
        str(data_root),
        "debug",
        "bundle",
        "invocation-1",
        "--db",
        str(db_path),
        "--output",
        "existing",
    ]
    assert cli.main(argv) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == (
        "BUNDLE_DESTINATION_EXISTS"
    )

    real_parent = data_root / "real-parent"
    real_parent.mkdir()
    (data_root / "linked-parent").symlink_to(real_parent, target_is_directory=True)
    argv[-1] = "linked-parent/bundle"
    assert cli.main(argv) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] in {
        "BUNDLE_SYMLINK_REFUSED",
        "BUNDLE_OUTPUT_OUTSIDE_ROOT",
    }
