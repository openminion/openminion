from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.e2e.cli.focus.test_live_simple_input_project import (
    LIVE_SCENARIOS,
    _approval_action_classes,
    _approval_events,
    _accepted_intervention_count,
    _assert_completed_child_lifecycle,
    _assert_required_completed_tools,
    _fixture,
    _project_owners,
    _unplanned_intervention_count,
)
from tests.e2e.runners.run_simple_input_long_coding_e2e import (
    FROZEN_LIVE_AGENT_ID,
    _ARTIFACT_ENV,
    _LIVE_TARGETS,
    _artifact_root,
    _clean_source_revision,
    _live_agent_id,
    _mnte_evidence,
    _scenario_evidence,
)


def test_live_corpus_has_the_three_spec_scenarios() -> None:
    assert tuple(LIVE_SCENARIOS) == (
        "plain-multifile-repair",
        "research-then-code",
        "delegated-read-only-review",
    )


def test_live_runner_executes_silc_and_mnte_in_one_process() -> None:
    assert _LIVE_TARGETS == (
        "tests/e2e/cli/focus/test_live_simple_input_project.py",
        "tests/e2e/cli/focus/test_live_model_neutral_tool_exposure.py",
    )


def test_live_summary_reads_mnte_evidence_from_shared_root(tmp_path) -> None:
    evidence_path = tmp_path / "focus" / "mnte-focus-live-evidence.json"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text(
        json.dumps({"scenario_id": "mnte-core", "disposition": "pass"}),
        encoding="utf-8",
    )

    assert _mnte_evidence(tmp_path, live_result=0)[0] == {
        "scenario_id": "mnte-core",
        "path": "focus/mnte-focus-live-evidence.json",
        "disposition": "pass",
    }
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


def test_live_agent_contract_requires_frozen_coding_profile() -> None:
    config = {
        "default_agent": FROZEN_LIVE_AGENT_ID,
        "agents": {
            FROZEN_LIVE_AGENT_ID: {"default_act_profile": "coding"},
        },
    }

    assert _live_agent_id(config) == FROZEN_LIVE_AGENT_ID
    config["agents"][FROZEN_LIVE_AGENT_ID]["default_act_profile"] = "auto"
    with pytest.raises(ValueError, match="coding act profile"):
        _live_agent_id(config)


def test_clean_source_revision_rejects_dirty_checkout(tmp_path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)

    assert _clean_source_revision(tmp_path)
    (tmp_path / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="clean source checkout"):
        _clean_source_revision(tmp_path)


def test_live_project_reader_uses_focus_generated_root(tmp_path) -> None:
    data_root = tmp_path / "data"
    generated_root = data_root / "runtime"
    probe = SimpleNamespace(
        data_root=data_root,
        session_id="focus-test",
        environment=lambda: {
            "OPENMINION_DATA_ROOT": str(data_root),
            "OPENMINION_GENERATED_ROOT": str(generated_root),
        },
    )

    store, manager = _project_owners(probe)
    try:
        assert store.root == generated_root / "state" / "task" / "autonomy"
    finally:
        manager.close()


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

    assert _approval_action_classes(transcript) == ["project.start", "project.start"]
    events = _approval_events(transcript)
    assert events == [
        {"sequence": 1, "action": "project.start", "decision": "session"},
        {"sequence": 2, "action": "project.start", "decision": "session"},
    ]
    assert _unplanned_intervention_count(events, ("project.start",)) == 1
    with pytest.raises(AssertionError, match="unexpected approval events"):
        _accepted_intervention_count(events, ("project.start",))
    assert _approval_action_classes("Project queued: run-1") == []
