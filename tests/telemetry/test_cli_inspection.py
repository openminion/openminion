from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import uuid

import pytest

from openminion.modules.telemetry.cli import main
from openminion.modules.telemetry.events import catalog
from openminion.modules.telemetry.invocation_inspection import structural_error_code
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"failure_code": "FAILED_DIRECT"}, "FAILED_DIRECT"),
        ({"error_code": "ERROR_DIRECT"}, "ERROR_DIRECT"),
        ({"error": {"code": "NESTED"}}, "NESTED"),
    ],
)
def test_structural_error_code_accepts_current_terminal_shapes(
    data: dict[str, Any],
    expected: str,
) -> None:
    assert structural_error_code(data) == expected


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
    assert payload["status"] == "ready"
    assert payload["paths"]["db_path"] == str(db_path.resolve(strict=False))
    assert payload["paths"]["trace_root"].endswith("/traces")
    assert payload["database"]["parent_writable"] is False
    assert payload["database"]["creatable"] is True
    assert payload["database"]["status"] == "ready"
    assert payload["otel_exporter"]["enabled"] is False
    assert payload["otel_exporter"]["status"] == "disabled"
    assert payload["otel_exporter"]["noncritical_queue_capacity"] == 1024
    assert payload["content_capture"] == {
        "exact_request_traces_enabled": False,
        "legacy_provider_debug_enabled": False,
        "local_telemetry_content_enabled": False,
        "otel_assistant_body_enabled": False,
        "otel_input_messages_enabled": False,
        "otel_output_messages_enabled": False,
        "otel_tool_content_enabled": False,
    }


def test_telemetryctl_doctor_reports_capture_posture_without_values(
    capsys,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home_root = tmp_path / "home"
    config_path = home_root / ".openminion" / "agents.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "runtime": {
                    "telemetry_exporter": {
                        "include_local_content": True,
                        "include_assistant_body": True,
                        "include_input_messages": True,
                        "include_output_messages": True,
                        "include_tool_content": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.setenv("OPENMINION_LLM_DEBUG", "1")
    monkeypatch.setenv("OPENMINION_LLM_DEBUG_PROVIDER", "private-provider-filter")

    assert main(["--home-root", str(home_root), "doctor"]) == 0

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["content_capture"] == {
        "exact_request_traces_enabled": True,
        "legacy_provider_debug_enabled": True,
        "local_telemetry_content_enabled": True,
        "otel_assistant_body_enabled": True,
        "otel_input_messages_enabled": True,
        "otel_output_messages_enabled": True,
        "otel_tool_content_enabled": True,
    }
    assert "private-provider-filter" not in output


def test_telemetryctl_doctor_reports_incomplete_external_exporter(
    capsys,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "home" / ".openminion" / "agents.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"runtime": {"telemetry_exporter": {"enabled": True}}}),
        encoding="utf-8",
    )

    assert main(["--home-root", str(tmp_path / "home"), "doctor"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "attention"
    assert payload["otel_exporter"]["status"] == "incomplete"
    assert payload["otel_exporter"]["endpoint_configured"] is False


def test_telemetryctl_rejects_non_positive_list_limit() -> None:
    with pytest.raises(SystemExit):
        main(["invocation", "list", "--limit", "0"])


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
    raw_file = trace_file.with_name("step01-call01-raw.txt")
    raw_file.write_text("exact request\n", encoding="utf-8")
    trace_file.with_name("notes.txt").write_text("not a trace\n", encoding="utf-8")

    assert main(["--home-root", str(tmp_path / "home"), "trace", "list"]) == 0
    listing = json.loads(capsys.readouterr().out)
    files = {item["path"]: item for item in listing["files"]}
    assert listing["count"] == 2
    assert files[
        "llm/agent-a/turn-session/step01-call01-structured.json"
    ]["kind"] == "structured"
    assert files["llm/agent-a/turn-session/step01-call01-raw.txt"]["kind"] == (
        "raw_request"
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
    assert "content" not in shown

    assert (
        main(
            [
                "--home-root",
                str(tmp_path / "home"),
                "trace",
                "show",
                "llm/agent-a/turn-session/step01-call01-structured.json",
                "--raw",
            ]
        )
        == 0
    )
    raw = json.loads(capsys.readouterr().out)
    assert raw["content"] == {"trace": {"trace_id": "trace-1"}}


def test_telemetryctl_trace_show_rejects_paths_outside_trace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))

    with pytest.raises(ValueError, match="non-symlinked trace artifact"):
        main(["--home-root", str(tmp_path / "home"), "trace", "show", "../secret.json"])


def test_telemetryctl_trace_show_rejects_symlinked_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    linked = data_root / "traces" / "llm" / "linked.json"
    linked.parent.mkdir(parents=True)
    linked.symlink_to(outside)

    with pytest.raises(ValueError, match="non-symlinked trace artifact"):
        main(
            [
                "--home-root",
                str(tmp_path / "home"),
                "trace",
                "show",
                "llm/linked.json",
            ]
        )


def test_telemetryctl_invocation_list_show_and_graph_are_structural(
    capsys,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    db_path = data_root / "telemetry.db"
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
            event_id="llm-completed-1",
            trace_key="trace-1",
            timestamp=1.0,
            data={
                "status": "ok",
                "operation": "chat",
                "model": "model-1",
                "llm_call_id": "call-1",
                "duration_ms": 30,
                "provider_round_trip_ms": 20,
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "cached_tokens": 2,
                },
                "cost_usd": 0.01,
                "cost_source": "provider",
                "content": "must not appear in CLI output",
                "thinking": "must not appear",
                "headers": {"authorization": "must not appear"},
                "url": "https://secret.example",
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
            event_id="policy-1",
            timestamp=2.0,
            data={"status": "deny", "decision": "deny", "reason_code": "scope"},
        )
    )
    service.record_event_sync(
        TelemetryEvent(
            session_id="session-1",
            turn_id="turn-1",
            invocation_id=invocation_id,
            execution_id=execution_id,
            agent_id="agent-1",
            event_type="agent.invocation.failed",
            event_id="failed-1",
            timestamp=3.0,
            data={
                "status": "failed",
                "error": {
                    "code": "FAIL_CODE",
                    "type": "FAIL_TYPE",
                    "category": "FAIL_CATEGORY",
                    "message": "must not appear",
                },
            },
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
    llm_row = next(row for row in shown["events"] if row["event_type"] == "llm.call.completed")
    assert llm_row == {
        "agent_id": "agent-1",
        "duration_ms": 30,
        "event_id": "llm-completed-1",
        "event_type": "llm.call.completed",
        "execution_id": execution_id,
        "invocation_id": invocation_id,
        "llm_call_id": "call-1",
        "model": "model-1",
        "operation": "chat",
        "provider_round_trip_ms": 20,
        "session_id": "session-1",
        "status": "ok",
        "timestamp": 1.0,
        "trace_key": "trace-1",
        "turn_id": "turn-1",
    }
    failed_row = next(
        row for row in shown["events"] if row["event_type"] == "agent.invocation.failed"
    )
    assert failed_row["error_code"] == "FAIL_CODE"
    assert "must not appear" not in shown_text
    assert "secret.example" not in shown_text

    assert main(["invocation", "graph", invocation_id, "--db", str(db_path)]) == 0
    graph = json.loads(capsys.readouterr().out)
    assert graph["segments"][0]["execution_id"] == execution_id
    assert "events" not in graph


def test_telemetryctl_invocation_rejects_out_of_grammar_identifier(
    capsys,
    tmp_path: Path,
) -> None:
    assert (
        main(
            [
                "invocation",
                "show",
                "../not-an-invocation",
                "--db",
                str(tmp_path / "telemetry.db"),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INVALID_ARGUMENT"
