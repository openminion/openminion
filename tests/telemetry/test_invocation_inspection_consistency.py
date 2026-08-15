from __future__ import annotations

import json
from pathlib import Path

from openminion.modules.telemetry.cli import main
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService


def _record_invocation(db_path: Path, invocation_id: str) -> None:
    service = TelemetryService(str(db_path))
    try:
        for event_type, timestamp, data in (
            ("agent.invocation.started", 10.0, {}),
            ("policy.decision", 11.0, {"decision": "allow"}),
            ("agent.invocation.completed", 12.0, {"status": "completed"}),
        ):
            service.record_event_sync(
                TelemetryEvent(
                    session_id="session-1",
                    turn_id="turn-1",
                    invocation_id=invocation_id,
                    execution_id="execution-1",
                    agent_id="agent-1",
                    event_type=event_type,
                    timestamp=timestamp,
                    data=data,
                )
            )
    finally:
        service.close_sync()


def test_invocation_list_missing_database_does_not_create_it(
    capsys,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "telemetry.db"

    assert main(["invocation", "list", "--db", str(db_path)]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "count": 0,
        "diagnostics": {"legacy_event_count": 0},
        "invocations": [],
    }
    assert not db_path.exists()


def test_invocation_filters_preserve_complete_canonical_summary(
    capsys,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "telemetry.db"
    invocation_id = "opaque.invocation-1"
    _record_invocation(db_path, invocation_id)

    assert (
        main(
            [
                "invocation",
                "list",
                "--event-type",
                "policy.decision",
                "--db",
                str(db_path),
            ]
        )
        == 0
    )
    listing = json.loads(capsys.readouterr().out)
    listed = listing["invocations"][0]
    assert listed["event_count"] == 3
    assert listed["summary"]["duration_ms"] == 2000

    assert (
        main(
            [
                "invocation",
                "show",
                invocation_id,
                "--event-type",
                "policy.decision",
                "--db",
                str(db_path),
            ]
        )
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["summary"] == listed["summary"]
    assert [event["event_type"] for event in shown["events"]] == [
        "policy.decision"
    ]

    assert (
        main(
            [
                "invocation",
                "graph",
                invocation_id,
                "--event-type",
                "policy.decision",
                "--db",
                str(db_path),
            ]
        )
        == 0
    )
    graph = json.loads(capsys.readouterr().out)
    assert graph["summary"] == listed["summary"]
    assert graph["event_filter"] == {
        "event_type": "policy.decision",
        "matched_event_count": 1,
    }


def test_invocation_duration_is_not_sum_of_overlapping_event_durations(
    capsys,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "telemetry.db"
    invocation_id = "invocation-overlap"
    service = TelemetryService(str(db_path))
    try:
        for index, (event_type, timestamp) in enumerate(
            (
                ("agent.invocation.started", 10.0),
                ("llm.call.completed", 11.0),
                ("tool.execution.completed", 11.5),
                ("agent.invocation.completed", 12.0),
            )
        ):
            service.record_event_sync(
                TelemetryEvent(
                    session_id="session-1",
                    turn_id="turn-1",
                    invocation_id=invocation_id,
                    event_type=event_type,
                    timestamp=timestamp,
                    event_id=f"event-{index}",
                    data={"duration_ms": 9000},
                )
            )
    finally:
        service.close_sync()

    assert main(["invocation", "list", "--db", str(db_path)]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["invocations"][0]["summary"]["duration_ms"] == 2000

    assert main(["invocation", "show", invocation_id, "--db", str(db_path)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["summary"]["duration_ms"] == 2000
