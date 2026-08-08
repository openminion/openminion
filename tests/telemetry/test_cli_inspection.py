from __future__ import annotations

import json
from pathlib import Path

import pytest

from openminion.modules.telemetry.cli import main
from openminion.modules.telemetry.events import catalog


def test_telemetryctl_catalog_prints_event_dispositions(capsys) -> None:
    assert main(["catalog"]) == 0

    payload = json.loads(capsys.readouterr().out)

    assert payload["event_count"] == len(catalog.EVENT_TYPES)
    event_rows = {row["event_type"]: row for row in payload["events"]}
    assert event_rows["llm.call.completed"]["otel_disposition"] == "span"
    assert event_rows["telemetry.queue.stats"]["otel_disposition"] == "metric"
    assert event_rows["message"]["otel_disposition"] == "excluded"


def test_telemetryctl_doctor_reports_paths_and_exporter_config(
    capsys,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    db_path = tmp_path / "data" / "telemetry" / "events.db"

    assert (
        main(["--home-root", str(tmp_path / "home"), "doctor", "--db", str(db_path)])
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["paths"]["db_path"] == str(db_path.resolve(strict=False))
    assert payload["paths"]["trace_root"].endswith("/traces")
    assert payload["database"]["parent_writable"] is False
    assert payload["otel_exporter"]["enabled"] is False
    assert payload["otel_exporter"]["noncritical_queue_capacity"] == 1024


def test_telemetryctl_trace_list_and_show(
    capsys,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    trace_file = (
        data_root
        / "traces"
        / "llm"
        / "agent-a"
        / "turn-session"
        / "step01-call01-structured.json"
    )
    trace_file.parent.mkdir(parents=True)
    trace_file.write_text(
        json.dumps({"trace": {"trace_id": "trace-1"}}), encoding="utf-8"
    )

    assert main(["--home-root", str(tmp_path / "home"), "trace", "list"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["count"] == 1
    assert listing["files"][0]["kind"] == "structured"
    assert (
        listing["files"][0]["path"]
        == "llm/agent-a/turn-session/step01-call01-structured.json"
    )

    assert (
        main(
            [
                "--home-root",
                str(tmp_path / "home"),
                "trace",
                "show",
                "llm/agent-a/turn-session/step01-call01-structured.json",
            ]
        )
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["content"] == {"trace": {"trace_id": "trace-1"}}


def test_telemetryctl_trace_show_rejects_paths_outside_trace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))

    with pytest.raises(ValueError, match="trace path must stay under trace root"):
        main(["--home-root", str(tmp_path / "home"), "trace", "show", "../secret.json"])
