from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parent
    / "skills"
    / "runners"
    / "run_skill_prerouting_resilience_report.py"
)


def _run_report(*args: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_report_reads_jsonl_events(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "session_id": "sess-a",
                        "ts": "2026-03-27T10:00:00Z",
                        "type": "skill.prerouting",
                        "payload": {
                            "latency_ms": 10,
                            "fail_closed_reason": None,
                        },
                    }
                ),
                json.dumps(
                    {
                        "session_id": "sess-a",
                        "ts": "2026-03-27T10:01:00Z",
                        "type": "skill.prerouting",
                        "payload": {
                            "latency_ms": 40,
                            "fail_closed_reason": "timeout",
                        },
                    }
                ),
                json.dumps(
                    {
                        "session_id": "sess-a",
                        "ts": "2026-03-27T10:02:00Z",
                        "type": "turn.user",
                        "payload": {"text": "hello"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = _run_report("--input", str(events_path))

    assert summary["total_events"] == 2
    assert summary["fail_closed_count"] == 1
    assert summary["fail_closed_reason_counts"] == {"timeout": 1}
    assert summary["latency_ms"]["p50"] == 25
    assert summary["latency_ms"]["p95"] == 38


def test_report_reads_sqlite_events(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE session_events (
                session_id TEXT,
                timestamp TEXT,
                event_type TEXT,
                payload_json TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO session_events VALUES (?, ?, ?, ?)",
            (
                "sess-b",
                "2026-03-27T11:00:00Z",
                "skill.prerouting",
                json.dumps({"latency_ms": 15, "fail_closed_reason": "rate_limited"}),
            ),
        )
        conn.execute(
            "INSERT INTO session_events VALUES (?, ?, ?, ?)",
            (
                "sess-b",
                "2026-03-27T11:01:00Z",
                "skill.prerouting",
                json.dumps({"latency_ms": 25, "fail_closed_reason": None}),
            ),
        )
        conn.execute(
            "INSERT INTO session_events VALUES (?, ?, ?, ?)",
            (
                "sess-b",
                "2026-03-27T11:02:00Z",
                "turn.user",
                json.dumps({"text": "ignore"}),
            ),
        )
        conn.commit()

    summary = _run_report("--input", str(db_path))

    assert summary["total_events"] == 2
    assert summary["fail_closed_count"] == 1
    assert summary["fail_closed_reason_counts"] == {"rate_limited": 1}
    assert summary["session_count"] == 1
    assert summary["latency_ms"]["max"] == 25


def test_report_scans_directories_for_supported_inputs(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess-local"
    session_dir.mkdir(parents=True)
    (session_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "session_id": "sess-c",
                "ts": "2026-03-27T12:00:00Z",
                "type": "skill.prerouting",
                "payload": {"latency_ms": 5, "fail_closed_reason": None},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "brain" / "sessions.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE session_events (
                session_id TEXT,
                timestamp TEXT,
                event_type TEXT,
                payload_json TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO session_events VALUES (?, ?, ?, ?)",
            (
                "sess-d",
                "2026-03-27T12:01:00Z",
                "skill.prerouting",
                json.dumps({"latency_ms": 50, "fail_closed_reason": "timeout"}),
            ),
        )
        conn.commit()

    summary = _run_report("--input", str(tmp_path))

    assert summary["total_events"] == 2
    assert summary["fail_closed_count"] == 1
    assert summary["fail_closed_reason_counts"] == {"timeout": 1}
    assert summary["source_count"] == 2
