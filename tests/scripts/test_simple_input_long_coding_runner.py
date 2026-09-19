from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

from tests.e2e.cli.focus.test_live_simple_input_project import (
    _fixture,
    _telemetry_evidence,
)
from tests.e2e.runners.run_simple_input_long_coding_e2e import (
    _ARTIFACT_ENV,
    _evidence,
    _root,
)


def test_configured_artifact_root_is_absolute(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert _root({_ARTIFACT_ENV: "artifacts"}) == tmp_path / "artifacts"


def test_live_summary_keeps_all_scenarios_visible(tmp_path) -> None:
    path = tmp_path / "silc-plain-restart-repair-evidence.json"
    path.write_text(json.dumps({"disposition": "pass"}), encoding="utf-8")

    evidence = _evidence(tmp_path, live_result=1)

    assert [item["disposition"] for item in evidence] == [
        "pass",
        "unavailable",
        "unavailable",
    ]


def test_local_summary_omits_unexecuted_live_scenarios(tmp_path) -> None:
    assert _evidence(tmp_path, live_result=None) == []


def test_telemetry_evidence_reads_canonical_event_data(tmp_path) -> None:
    database = tmp_path / "telemetry" / "telemetry.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, event_type TEXT, data TEXT)"
        )
        connection.executemany(
            "INSERT INTO events (event_type, data) VALUES (?, ?)",
            [
                (
                    "tool.call.requested",
                    json.dumps(
                        {
                            "call_id": "delegate-1",
                            "canonical_name": "task.delegate",
                        }
                    ),
                ),
                (
                    "tool.call.completed",
                    json.dumps({"call_id": "delegate-1", "output": {"ok": True}}),
                ),
                (
                    "chat.phase_timing",
                    json.dumps(
                        {
                            "provider_calls_total": 2,
                            "provider_input_tokens": 10,
                            "provider_output_tokens": 3,
                        }
                    ),
                ),
            ],
        )

    evidence = _telemetry_evidence(tmp_path)

    assert evidence["tool_sequence"] == ["task.delegate"]
    assert evidence["delegation_results"] == [{"ok": True}]
    assert evidence["provider_calls"] == 2


def test_restart_fixture_failure_is_owned_by_project_verifier(tmp_path) -> None:
    repo = tmp_path / "fixture"
    _fixture(repo, "plain-restart-repair")
    (repo / "calculator.py").write_text(
        "def add(left, right):\n    return left + right\n", encoding="utf-8"
    )
    (repo / "formatting.py").write_text(
        "def title(text):\n    return text.title()\n", encoding="utf-8"
    )
    command = [sys.executable, "verify_once.py"]

    assert subprocess.run(command, cwd=repo, check=False).returncode == 0
    verifier_env = {**os.environ, "OPENMINION_SILC_PROJECT_VERIFIER": "1"}
    assert (
        subprocess.run(command, cwd=repo, env=verifier_env, check=False).returncode
        == 1
    )
    assert (
        subprocess.run(command, cwd=repo, env=verifier_env, check=False).returncode
        == 0
    )
