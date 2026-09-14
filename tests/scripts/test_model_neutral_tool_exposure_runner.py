from __future__ import annotations

import json
import sqlite3

from tests.e2e.cli.focus.test_live_model_neutral_tool_exposure import (
    _failure_disposition,
    _provider_failure_categories,
)
from tests.e2e.runners.run_model_neutral_tool_exposure_e2e import (
    _scenario_evidence,
)


def test_live_summary_marks_each_missing_scenario_unavailable(tmp_path) -> None:
    evidence = _scenario_evidence(tmp_path, live_result=0)

    assert evidence == [
        {
            "path": None,
            "disposition": "unavailable",
            "scenario_id": "mnte-core-edit-test",
            "scenario_results": [],
        },
        {
            "path": None,
            "disposition": "unavailable",
            "scenario_id": "mnte-project-corpus",
            "scenario_results": [],
        },
    ]


def test_live_summary_keeps_missing_scenario_visible(tmp_path) -> None:
    focus_path = tmp_path / "focus" / "mnte-focus-live-evidence.json"
    focus_path.parent.mkdir()
    focus_path.write_text(
        json.dumps(
            {
                "scenario_id": "mnte-core-edit-test",
                "disposition": "pass",
                "scenario_results": [],
            }
        ),
        encoding="utf-8",
    )

    evidence = _scenario_evidence(tmp_path, live_result=1)

    assert [item["scenario_id"] for item in evidence] == [
        "mnte-core-edit-test",
        "mnte-project-corpus",
    ]
    assert evidence[1]["disposition"] == "unavailable"


def test_live_failure_uses_typed_llm_terminal_fact(tmp_path) -> None:
    telemetry_path = tmp_path / "telemetry.db"
    with sqlite3.connect(telemetry_path) as connection:
        connection.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, event_type TEXT, data TEXT)"
        )
        connection.execute(
            "INSERT INTO events (event_type, data) VALUES (?, ?)",
            (
                "brain.execution_status",
                json.dumps({"adaptive.termination_reason": "llm_error"}),
            ),
        )

    categories = _provider_failure_categories(telemetry_path)

    assert categories == ["LLM_ERROR"]
    assert _failure_disposition(categories) == "provider_residual"
    assert _failure_disposition([]) == "failed"
