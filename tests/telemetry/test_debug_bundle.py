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
    service = TelemetryService(db_path, include_local_content=True)

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
                    "operation": "agent_turn",
                    "tool_name": "exec.run",
                    "duration_ms": 1000,
                    "error": {
                        "code": "TEST_CODE",
                        "type": "TEST_FAILURE",
                        "message": "private free-form failure",
                    },
                },
            ),
            TelemetryEvent(
                session_id="private-session",
                turn_id="private-turn",
                invocation_id=invocation_id,
                execution_id="private-execution",
                agent_id="private-agent",
                event_id="private-report",
                event_type="tool.execution.completed",
                timestamp=1.5,
                data={
                    "status": "ok",
                    "assessment_id": "b" * 32,
                    "result_status": "partial",
                    "tool_name": "security.publish_report",
                    "duration_ms": 25,
                    "check_count": 4,
                    "finding_count": 1,
                    "candidate_count": 1,
                    "rejected_count": 0,
                    "artifact_count": 1,
                    "artifact_refs": ["artifact://sha256/" + ("a" * 64)],
                    "report_body": "private report prose",
                    "source_body": "private source body",
                    "scanner_output": "private scanner output",
                    "credential": "private credential",
                },
            ),
            TelemetryEvent(
                session_id="private-session",
                turn_id="private-turn",
                invocation_id=invocation_id,
                execution_id="private-execution",
                agent_id="private-agent",
                event_id="private-brain-status",
                event_type="brain.execution_status",
                timestamp=1.75,
                data={
                    "status": "completed",
                    "tool_results": [
                        {
                            "structural_only": True,
                            "tool_name": "security.scan_code",
                            "ok": True,
                            "verified": True,
                            "data": {
                                "assessment_id": "b" * 32,
                                "result_status": "partial",
                                "finding_count": 1,
                                "artifact_refs": ["artifact://sha256/" + ("c" * 64)],
                                "source_body": "private nested source body",
                            },
                            "source": "adaptive",
                            "private_result": "private nested result prose",
                        },
                        {
                            "tool_name": "file.read",
                            "data": {"content": "private file content"},
                        },
                    ],
                },
            ),
        ):
            await service.record_event(event)
        await service.close()

    asyncio.run(record())
    return db_path


def _set_data_root(monkeypatch: pytest.MonkeyPatch, data_root: Path) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    monkeypatch.setenv("OPENMINION_GENERATED_ROOT", str(data_root / "runtime"))


def test_bundle_cli_writes_private_atomic_sanitized_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root = tmp_path / "data"
    _set_data_root(monkeypatch, data_root)
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
        "private report prose",
        "private source body",
        "private scanner output",
        "private credential",
        "private nested source body",
        "private nested result prose",
        "private file content",
        str(tmp_path),
    ):
        assert excluded not in combined
    assert "id-001" in combined
    summary = json.loads((destination / "invocation-summary.json").read_text())
    graph = json.loads((destination / "invocation-graph.json").read_text())
    assert summary["failure_code"] == "TEST_CODE"
    terminal = next(
        row for row in graph["events"] if row["event_type"] == "agent.invocation.failed"
    )
    assert terminal["error_code"] == "TEST_CODE"
    assert terminal["operation"] == "agent_turn"
    assert terminal["tool_name"] == "exec.run"
    assert terminal["duration_ms"] == 1000
    report_event = next(
        row
        for row in graph["events"]
        if row.get("tool_name") == "security.publish_report"
    )
    assert report_event["result_status"] == "partial"
    assert report_event["assessment_id"] == "b" * 32
    assert report_event["check_count"] == 4
    assert report_event["finding_count"] == 1
    assert report_event["candidate_count"] == 1
    assert report_event["artifact_count"] == 1
    assert report_event["artifact_refs"] == ["artifact://sha256/" + ("a" * 64)]
    brain_event = next(
        row for row in graph["events"] if row["event_type"] == "brain.execution_status"
    )
    assert brain_event["tool_results"] == [
        {
            "tool_name": "security.scan_code",
            "ok": True,
            "verified": True,
            "data": {
                "result_status": "partial",
                "assessment_id": "b" * 32,
                "finding_count": 1,
                "artifact_refs": ["artifact://sha256/" + ("c" * 64)],
            },
            "error_code": "",
            "call_id": "",
            "source": "adaptive",
        }
    ]


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
    _set_data_root(monkeypatch, data_root)
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
    _set_data_root(monkeypatch, data_root)
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
