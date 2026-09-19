from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests.e2e.cli.focus.test_live_simple_input_project import (
    LIVE_SCENARIOS,
    _approval_action_classes,
    _assert_completed_child_lifecycle,
    _assert_required_completed_tools,
    _fixture,
)
from tests.e2e.runners.run_simple_input_long_coding_e2e import (
    _ARTIFACT_ENV,
    _artifact_root,
    _scenario_evidence,
)


def test_live_corpus_has_the_three_spec_scenarios() -> None:
    assert tuple(LIVE_SCENARIOS) == (
        "plain-multifile-repair",
        "research-then-code",
        "delegated-read-only-review",
    )
    assert "first project cycle" in LIVE_SCENARIOS["plain-multifile-repair"]
    assert "web.search" in LIVE_SCENARIOS["research-then-code"]
    assert "web.fetch" in LIVE_SCENARIOS["research-then-code"]
    assert (
        "independent read-only review" in LIVE_SCENARIOS["delegated-read-only-review"]
    )
    assert "accept a passing artifact" in LIVE_SCENARIOS["delegated-read-only-review"]
    assert (
        "implements both functions in their two files"
        in LIVE_SCENARIOS["delegated-read-only-review"]
    )


def test_delegated_review_fixture_requires_two_implementation_files(tmp_path) -> None:
    workspace, evidence = _fixture(tmp_path, "delegated-read-only-review")

    assert evidence["expected_changed_paths"] == ["calc.py", "operations.py"]
    (workspace / "calc.py").write_text(
        "def add(left, right):\n    return left + right\n", encoding="utf-8"
    )
    one_file = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    assert one_file.returncode != 0
    (workspace / "operations.py").write_text(
        "def multiply(left, right):\n    return left * right\n", encoding="utf-8"
    )
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    )


def test_configured_artifact_root_is_absolute(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert _artifact_root({_ARTIFACT_ENV: "artifacts"}) == tmp_path / "artifacts"


def test_live_summary_marks_every_missing_scenario_unavailable(tmp_path) -> None:
    assert _scenario_evidence(tmp_path, live_result=1) == [
        {
            "scenario_id": scenario_id,
            "path": None,
            "disposition": "unavailable",
        }
        for scenario_id in LIVE_SCENARIOS
    ]


def test_live_summary_reads_each_scenario_disposition(tmp_path) -> None:
    for scenario_id in LIVE_SCENARIOS:
        (tmp_path / f"{scenario_id}-evidence.json").write_text(
            json.dumps({"scenario_id": scenario_id, "disposition": "pass"}),
            encoding="utf-8",
        )

    assert _scenario_evidence(tmp_path, live_result=0) == [
        {
            "scenario_id": scenario_id,
            "path": f"{scenario_id}-evidence.json",
            "disposition": "pass",
        }
        for scenario_id in LIVE_SCENARIOS
    ]


@pytest.mark.parametrize("event", ["tool.call.requested", "tool.call.blocked"])
def test_research_oracle_rejects_search_without_successful_completion(event) -> None:
    calls = [
        {"event": event, "tool": tool, "status": "blocked"}
        for tool in ("web.search", "web.fetch")
    ]

    with pytest.raises(AssertionError):
        _assert_required_completed_tools(calls, {"web.search", "web.fetch"})


def test_child_oracle_rejects_unresolved_or_digestless_child() -> None:
    valid = [
        {
            "mode": "sync",
            "status": "completed",
            "child_agent_id": "implementer",
            "record_alias": "artifact-1",
            "target_digest": "digest-1",
        },
        {
            "mode": "review",
            "status": "passed",
            "child_agent_id": "reviewer",
            "record_alias": "artifact-1",
            "target_digest": "digest-1",
            "findings": [],
        },
        {
            "mode": "accept",
            "status": "accepted",
            "record_alias": "artifact-1",
            "target_digest": "digest-1",
        },
    ]
    incomplete = valid[:-1]
    digestless = [*valid]
    digestless[1] = {**digestless[1], "target_digest": None}
    non_independent = [*valid]
    non_independent[1] = {
        **non_independent[1],
        "child_agent_id": "implementer",
    }
    mismatched_accept = [*valid]
    mismatched_accept[2] = {
        **mismatched_accept[2],
        "target_digest": "other-digest",
    }
    duplicate_accept = [*valid, valid[2]]
    failed_sync = [*valid]
    failed_sync[0] = {**failed_sync[0], "status": "failed"}

    _assert_completed_child_lifecycle(valid, parent_agent_id="parent")
    with pytest.raises(AssertionError):
        _assert_completed_child_lifecycle(incomplete, parent_agent_id="parent")
    with pytest.raises(AssertionError):
        _assert_completed_child_lifecycle(digestless, parent_agent_id="parent")
    with pytest.raises(AssertionError):
        _assert_completed_child_lifecycle(non_independent, parent_agent_id="parent")
    with pytest.raises(AssertionError):
        _assert_completed_child_lifecycle(mismatched_accept, parent_agent_id="parent")
    with pytest.raises(AssertionError):
        _assert_completed_child_lifecycle(duplicate_accept, parent_agent_id="parent")
    with pytest.raises(AssertionError):
        _assert_completed_child_lifecycle(failed_sync, parent_agent_id="parent")


def test_approval_classes_are_derived_from_visible_focus_prompts() -> None:
    transcript = (
        "Approval required: project.start(goal=fixture)\n"
        "Approval required: project.start(goal=fixture)\n"
    )

    assert _approval_action_classes(transcript) == ["project.start"]
    assert _approval_action_classes("Project queued: run-1") == []
