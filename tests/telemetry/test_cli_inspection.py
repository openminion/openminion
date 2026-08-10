from __future__ import annotations

import json
from pathlib import Path
import uuid

import pytest

from openminion.modules.telemetry.cli import main
from openminion.modules.telemetry.events import catalog
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService


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


def test_telemetryctl_invocation_list_show_and_graph_are_structural(
    capsys,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "telemetry.db"
    invocation_id = str(uuid.uuid4())
    execution_id = str(uuid.uuid4())
    service = TelemetryService(str(db_path), include_local_content=True)
    service.record_event_sync(
        TelemetryEvent(
            session_id="session-1",
            turn_id="turn-1",
            invocation_id=invocation_id,
            execution_id=execution_id,
            agent_id="agent-1",
            event_type="llm.call.completed",
            data={
                "status": "ok",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "cached_tokens": 2,
                },
                "cost_usd": 0.01,
                "cost_source": "provider",
                "content": "must not appear in CLI output",
            },
        )
    )
    service.record_event_sync(
        TelemetryEvent(
            session_id="session-1",
            turn_id="turn-1",
            invocation_id=invocation_id,
            execution_id=execution_id,
            agent_id="agent-1",
            event_type="policy.decision",
            data={"status": "deny", "decision": "deny", "reason_code": "scope"},
        )
    )
    service.close_sync()

    assert main(["invocation", "list", "--db", str(db_path)]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["count"] == 1
    assert listing["invocations"][0]["invocation_id"] == invocation_id

    assert main(["invocation", "show", invocation_id, "--db", str(db_path)]) == 0
    shown_text = capsys.readouterr().out
    shown = json.loads(shown_text)
    assert shown["summary"]["input_tokens"] == 10
    assert shown["summary"]["cost_usd"] == 0.01
    assert shown["summary"]["policy_decisions"] == {"deny": 1}
    assert "must not appear" not in shown_text

    assert main(["invocation", "graph", invocation_id, "--db", str(db_path)]) == 0
    graph = json.loads(capsys.readouterr().out)
    assert graph["segments"][0]["execution_id"] == execution_id
    assert "events" not in graph


def test_telemetryctl_invocation_rejects_non_uuid_identifier(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        main(
            [
                "invocation",
                "show",
                "../not-an-invocation",
                "--db",
                str(tmp_path / "telemetry.db"),
            ]
        )
