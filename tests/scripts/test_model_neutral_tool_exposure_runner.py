from __future__ import annotations

import json
import sqlite3

import pytest

from tests.e2e.cli.focus.test_live_model_neutral_tool_exposure import (
    _core_turn_evidence,
    _failure_disposition,
    _provider_failure_categories,
    _turn_local_tool_results,
)
from tests.e2e.runners.run_model_neutral_tool_exposure_e2e import (
    _ARTIFACT_ENV,
    _artifact_root,
    _scenario_evidence,
)


def test_configured_artifact_root_is_absolute(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    root = _artifact_root({_ARTIFACT_ENV: "artifacts"})

    assert root == tmp_path / "artifacts"


def test_core_evidence_counts_all_scoped_turns_and_failed_attempts() -> None:
    rows = []
    for turn_id, tokens in (("first", 10), ("second", 20), ("foreign", 999)):
        rows.extend(
            [
                (
                    turn_id,
                    "chat.phase_timing",
                    {
                        "provider_calls_total": 1,
                        "provider_call_purposes": ["act"],
                        "provider_attempts": [
                            {"outcome": "error", "error_code": "TIMEOUT"}
                        ],
                        "provider_input_tokens": tokens,
                        "provider_output_tokens": 2,
                        "total_turn_ms": tokens,
                    },
                ),
                (
                    turn_id,
                    "brain.execution_status",
                    {
                        "tool_schema_shortlisting.initial_active_count": 7,
                        "tool_schema_shortlisting.control_schema_count": 2,
                    },
                ),
                (turn_id, "tool.call.requested", {"canonical_name": "file.write"}),
            ]
        )

    evidence = _core_turn_evidence(rows, {"first", "second"}, "result: 1 passed")

    assert evidence["provider_calls"] == 2
    assert evidence["input_tokens"] == 30
    assert evidence["output_tokens"] == 4
    assert evidence["wall_time_ms"] == 30
    assert len(evidence["provider_attempts"]) == 2
    assert evidence["tool_sequence"] == ["file.write", "file.write"]


@pytest.mark.parametrize("tool_name", ["tool.request", "web.search"])
def test_core_evidence_rejects_optional_requests_even_when_denied(tool_name) -> None:
    with pytest.raises(AssertionError, match="core-only"):
        _core_turn_evidence(
            [("core", "tool.call.requested", {"canonical_name": tool_name})],
            {"core"},
            "result: 1 passed",
        )


@pytest.mark.parametrize("initial,control", [(8, 2), (7, 3)])
def test_core_evidence_enforces_schema_bounds_for_each_turn(initial, control) -> None:
    with pytest.raises(AssertionError):
        _core_turn_evidence(
            [
                (
                    "core",
                    "brain.execution_status",
                    {
                        "tool_schema_shortlisting.initial_active_count": initial,
                        "tool_schema_shortlisting.control_schema_count": control,
                    },
                )
            ],
            {"core"},
            "result: 1 passed",
        )


def test_core_evidence_requires_exact_terminal_marker() -> None:
    with pytest.raises(AssertionError, match="result:"):
        _core_turn_evidence([], {"core"}, "The result is passing")


def test_core_evidence_does_not_accept_prompt_echo_as_a_result() -> None:
    with pytest.raises(AssertionError, match="result:"):
        _core_turn_evidence(
            [], {"core"}, "Please finish with the label result: and count"
        )


def test_core_evidence_requires_provider_timing() -> None:
    with pytest.raises(AssertionError, match="timing"):
        _core_turn_evidence([], {"core"}, "result: 1 passed")


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


def test_live_failure_requires_typed_provider_fact(tmp_path) -> None:
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

    assert categories == []
    assert _failure_disposition(categories) == "failed"

    with sqlite3.connect(telemetry_path) as connection:
        connection.execute(
            "INSERT INTO events (event_type, data) VALUES (?, ?)",
            (
                "module.stats",
                json.dumps(
                    {
                        "module_id": "openminion-llm",
                        "operation": "error",
                        "error_code": "RATE_LIMITED",
                    }
                ),
            ),
        )

    categories = _provider_failure_categories(telemetry_path)

    assert categories == ["RATE_LIMITED"]
    assert _failure_disposition(categories) == "provider_residual"


def test_live_failure_reads_terminal_provider_error(tmp_path) -> None:
    telemetry_path = tmp_path / "telemetry.db"
    with sqlite3.connect(telemetry_path) as connection:
        connection.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, event_type TEXT, data TEXT)"
        )
        connection.execute(
            "INSERT INTO events (event_type, data) VALUES (?, ?)",
            (
                "agent.turn.failed",
                json.dumps({"error": {"code": "EMPTY_PROVIDER_RESPONSE"}}),
            ),
        )

    categories = _provider_failure_categories(telemetry_path)

    assert categories == ["EMPTY_PROVIDER_RESPONSE"]
    assert _failure_disposition(categories) == "provider_residual"


def test_live_evidence_removes_results_replayed_by_later_turns() -> None:
    first = {"call_id": "call-1", "tool_name": "web.search"}
    second = {"call_id": "call-2", "tool_name": "file.write"}

    results = _turn_local_tool_results(
        [
            {"tool_results": json.dumps([first])},
            {"tool_results": json.dumps([first, second])},
        ]
    )

    assert results == [[first], [second]]
