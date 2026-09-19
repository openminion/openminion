from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

import pytest

from openminion.cli.commands.daemon import (
    build_daemon_status_payload,
    daemon_start,
    daemon_stop,
)
from openminion.modules.task import (
    AutonomyRunStore,
    TaskManager,
    build_project_report_from_task,
    load_latest_project_checkpoint,
)
from openminion.modules.task.autonomy import (
    AutonomyRunStatus,
    resolve_autonomy_state_root,
)
from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
from openminion.modules.task.project.models import ProjectVerificationState
from tests.e2e.cli.focus.conftest import require_complex_focus
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.scenarios import FocusScenario

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(1800)]

_ARTIFACT_ENV = "OPENMINION_SILC_E2E_ARTIFACT_ROOT"
_RUN_ID_RE = re.compile(r"Project queued:\s*(\S+)")
_PYPA_GUIDE_URL = (
    "https://packaging.python.org/en/latest/guides/writing-pyproject-toml/"
)

LIVE_SCENARIOS = {
    "plain-multifile-repair": (
        "Work only in this Git repository. Treat this as durable project work and "
        "propose a typed project handoff for approval before changing files. The "
        "measurable outcome is to implement stats.total(values) in stats.py and "
        "stats.mean(values) in mean.py so `python -m pytest -q` passes. Use at most "
        "4 project iterations and 24 project tool calls. In the first project cycle, "
        "implement total correctly, write exactly `total-implemented` plus a newline "
        "to accepted_steps.log, but make mean return 0. Run the verifier, preserve "
        "the failed result, and request another cycle. Do not fix mean in that first "
        "cycle. After the failed checkpoint is resumed, repair mean without touching "
        "accepted_steps.log, rerun the verifier, inspect Git status, and complete "
        "only from passing verifier and repository facts."
    ),
    "research-then-code": (
        "Work only in this Git repository. Treat this as durable project work and "
        "propose a typed project handoff for approval before changing files. Before "
        "choosing the implementation, call web.search to discover and web.fetch to "
        "read the current official PyPA guide for writing pyproject.toml. Record its "
        "canonical URL in source_info.py as SOURCE_URL, then run "
        "`python -m pytest -q`, inspect Git status, and complete only when the test "
        "passes. Use at most 3 project iterations and 20 project tool calls."
    ),
    "delegated-read-only-review": (
        "Work only in this Git repository. Treat this as durable project work and "
        "propose a typed project handoff for approval before changing files. "
        "Implement calc.add in calc.py and operations.multiply in operations.py so "
        "`python -m pytest -q` passes. Delegate one bounded code-bearing subtask that "
        "implements both functions in their two files to the exact agent "
        "minimax-m2-7-highspeed so it returns a child worktree artifact. The parent "
        "does not edit those files. Then use the "
        "existing task.delegate review mode with the exact distinct reviewer "
        "minimax-m2-5-highspeed for one independent read-only review of that artifact "
        "against the tests. Inspect the review findings and explicitly "
        "accept a passing artifact as the parent; reject it if review fails. Run the "
        "verifier after the "
        "parent disposition, inspect Git status, and complete only from the accepted "
        "artifact, passing verifier, and repository facts. Use at most 4 project "
        "iterations and 28 project tool calls."
    ),
}


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _git(workspace: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(workspace), *args], text=True
    ).strip()


def _fixture(root: Path, scenario_id: str) -> tuple[Path, dict[str, object]]:
    workspace = root / "workspaces" / scenario_id
    workspace.mkdir(parents=True)
    if scenario_id == "plain-multifile-repair":
        files = {
            "accepted_steps.log": "pending\n",
            "stats.py": "def total(values):\n    raise NotImplementedError\n",
            "mean.py": "def mean(values):\n    raise NotImplementedError\n",
            "test_stats.py": (
                "from mean import mean\nfrom stats import total\n\n"
                "def test_stats():\n"
                "    assert total([2, 4, 6]) == 12\n"
                "    assert mean([2, 4, 6]) == 4\n"
            ),
        }
    elif scenario_id == "research-then-code":
        files = {
            "source_info.py": 'SOURCE_URL = ""\n',
            "test_source_info.py": (
                "from source_info import SOURCE_URL\n\n"
                "def test_source_url():\n"
                f"    assert SOURCE_URL == {_PYPA_GUIDE_URL!r}\n"
            ),
        }
    else:
        files = {
            "calc.py": "def add(left, right):\n    raise NotImplementedError\n",
            "operations.py": (
                "def multiply(left, right):\n    raise NotImplementedError\n"
            ),
            "test_calc.py": (
                "from calc import add\nfrom operations import multiply\n\n"
                "def test_calc():\n"
                "    assert add(2, 3) == 5\n"
                "    assert multiply(4, 5) == 20\n"
            ),
        }
    files[".gitignore"] = ".pytest_cache/\n__pycache__/\n"
    for name, content in files.items():
        (workspace / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.email", "silc@example.invalid"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "SILC Fixture"], cwd=workspace, check=True
    )
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed fixture"], cwd=workspace, check=True
    )
    fixture_text = "".join(f"{name}\0{files[name]}\0" for name in sorted(files))
    changed_paths = {
        "plain-multifile-repair": ["accepted_steps.log", "mean.py", "stats.py"],
        "research-then-code": ["source_info.py"],
        "delegated-read-only-review": ["calc.py", "operations.py"],
    }[scenario_id]
    oracle_text = (
        "python -m pytest -q\0"
        + fixture_text
        + json.dumps(changed_paths, separators=(",", ":"))
        + (
            "\0accepted_steps_mtime_ns_unchanged"
            if scenario_id == "plain-multifile-repair"
            else ""
        )
    )
    return workspace, {
        "fixture_sha256": _digest(fixture_text),
        "oracle_sha256": _digest(oracle_text),
        "expected_changed_paths": changed_paths,
    }


def _source_revision() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[4],
        text=True,
    ).strip()


def _project_owners(probe: FocusProbe) -> tuple[AutonomyRunStore, TaskManager]:
    home_root = probe.data_root.parent / "home-roots" / probe.session_id
    store = AutonomyRunStore(root=resolve_autonomy_state_root(home_root))
    manager = TaskManager.for_lifecycle_db(
        db_path=probe.data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH
    )
    return store, manager


def _wait_for_run(probe: FocusProbe, run_id: str, predicate, *, timeout: int = 900):
    store, manager = _project_owners(probe)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            run = store.require(run_id)
            checkpoint = load_latest_project_checkpoint(manager, task_id=run.task_id)
            if checkpoint is not None and predicate(run, checkpoint):
                return run, checkpoint
            time.sleep(0.1)
    finally:
        manager.close()
    raise AssertionError(f"project {run_id} did not reach the required checkpoint")


def _telemetry_cursor(probe: FocusProbe) -> int:
    path = probe.data_root / "telemetry" / "telemetry.db"
    if not path.is_file():
        return 0
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
    return int(row[0])


def _tool_evidence(
    probe: FocusProbe, *, after_event_id: int
) -> tuple[list[dict[str, object]], list[dict[str, object]], int]:
    telemetry_path = probe.data_root / "telemetry" / "telemetry.db"
    with sqlite3.connect(telemetry_path) as connection:
        tool_rows = connection.execute(
            "SELECT event_type, data FROM events WHERE id > ? AND agent_id = ? "
            "AND event_type IN ('tool.call.requested', 'tool.call.completed', "
            "'tool.call.blocked') "
            "ORDER BY id",
            (after_event_id, probe.agent_id),
        ).fetchall()
        timing_rows = connection.execute(
            "SELECT event_type, data FROM events WHERE id > ? AND session_id = ? "
            "AND agent_id = ? AND event_type = 'chat.phase_timing' ORDER BY id",
            (after_event_id, probe.session_id, probe.agent_id),
        ).fetchall()
    events = [(kind, json.loads(raw)) for kind, raw in (*tool_rows, *timing_rows)]
    requested = {
        str(event.get("call_id")): event
        for kind, event in events
        if kind == "tool.call.requested"
    }
    calls = [
        {
            "event": kind,
            "tool": event.get("canonical_name")
            or requested.get(str(event.get("call_id")), {}).get("canonical_name"),
            "call_id": event.get("call_id"),
            "mode": (event.get("sanitized_normalized_arguments") or {}).get("mode"),
            "status": event.get("status"),
        }
        for kind, event in events
        if kind != "chat.phase_timing"
    ]
    request_modes = {
        str(event.get("call_id")): (
            event.get("sanitized_normalized_arguments") or {}
        ).get("mode")
        for kind, event in events
        if kind == "tool.call.requested"
        and event.get("canonical_name") == "task.delegate"
    }
    child_state = []
    for kind, event in events:
        call_id = str(event.get("call_id", ""))
        if kind != "tool.call.completed" or call_id not in request_modes:
            continue
        output = event.get("output") or {}
        result = output.get("outputs") or {}
        nested = result.get("outputs") or {}
        review_receipt = result.get("review_receipt")
        review_findings = (
            review_receipt.get("findings") if isinstance(review_receipt, dict) else None
        )
        arguments = requested[call_id].get("sanitized_normalized_arguments") or {}
        requested_artifact = arguments.get("child_artifact") or {}
        artifact = result.get("child_artifact") or nested.get("child_artifact") or {}
        child_state.append(
            {
                "call_id": call_id,
                "mode": request_modes[call_id],
                "status": result.get("status"),
                "child_agent_id": result.get("agent_id")
                or nested.get("child_agent_id"),
                "record_alias": artifact.get("record_alias")
                or requested_artifact.get("record_alias"),
                "target_digest": result.get("target_digest")
                or artifact.get("target_digest")
                or requested_artifact.get("target_digest"),
                "findings": review_findings,
                "finding_count": (
                    len(review_findings) if isinstance(review_findings, list) else None
                ),
            }
        )
    tokens = sum(
        int(event.get("provider_input_tokens", 0))
        + int(event.get("provider_output_tokens", 0))
        for kind, event in events
        if kind == "chat.phase_timing"
    )
    return calls, child_state, tokens


def _assert_required_completed_tools(
    calls: list[dict[str, object]], required: set[str]
) -> None:
    completed = {
        str(item["tool"])
        for item in calls
        if item["event"] == "tool.call.completed" and item["status"] == "success"
    }
    assert required <= completed


def _approval_action_classes(transcript: str) -> list[str]:
    return sorted(
        set(re.findall(r"Approval required:\s*([A-Za-z0-9_.-]+)\(", transcript))
    )


def _assert_completed_child_lifecycle(
    child_state: list[dict[str, object]], *, parent_agent_id: str
) -> None:
    assert [item["mode"] for item in child_state] == ["sync", "review", "accept"]
    by_mode = {str(item["mode"]): item for item in child_state}
    assert by_mode["sync"]["status"] == "completed"
    assert by_mode["review"]["status"] == "passed"
    assert by_mode["accept"]["status"] == "accepted"
    assert isinstance(by_mode["review"]["findings"], list)
    implementer = str(by_mode["sync"]["child_agent_id"] or "")
    reviewer = str(by_mode["review"]["child_agent_id"] or "")
    assert implementer and reviewer
    assert len({implementer, reviewer, parent_agent_id}) == 3
    artifacts = child_state
    assert all(item["record_alias"] for item in artifacts)
    assert all(item["target_digest"] for item in artifacts)
    aliases = {str(item["record_alias"]) for item in artifacts if item["record_alias"]}
    digests = {
        str(item["target_digest"]) for item in artifacts if item["target_digest"]
    }
    assert len(aliases) == len(digests) == 1


def _scenario_evidence(
    probe: FocusProbe,
    workspace: Path,
    scenario_id: str,
    fixture_evidence: dict[str, object],
    run_id: str,
    elapsed_ms: int,
    after_event_id: int,
    verification_result: dict[str, object],
    approval_classes: list[str],
    *,
    process_boundary: dict[str, object] | None = None,
) -> dict[str, object]:
    store, manager = _project_owners(probe)
    try:
        run = store.require(run_id)
        report = build_project_report_from_task(manager, task_id=run.task_id)
        checkpoint = load_latest_project_checkpoint(manager, task_id=run.task_id)
    finally:
        manager.close()
    calls, child_state, tokens = _tool_evidence(probe, after_event_id=after_event_id)
    config = json.loads(probe.config_path.read_text(encoding="utf-8"))
    agent = config["agents"][probe.agent_id]
    provider = agent["provider"]
    provider_config = {
        **config["providers"][provider],
        **agent.get("provider_config_overrides", {}),
    }
    plan = checkpoint.payload.get("task_plan") if checkpoint else None
    return {
        "scenario_id": scenario_id,
        "source_revision": _source_revision(),
        "config_sha256": sha256(probe.config_path.read_bytes()).hexdigest(),
        "profile": probe.agent_id,
        "configured_act_profile": agent.get("default_act_profile"),
        "provider": provider,
        "model": provider_config["model"],
        "prompt": LIVE_SCENARIOS[scenario_id],
        "prompt_sha256": _digest(LIVE_SCENARIOS[scenario_id]),
        **fixture_evidence,
        "budgets": {
            "iterations": run.continuation_policy.max_iterations,
            "wall_clock_ms": run.continuation_policy.max_wall_clock_ms,
            "tool_calls": run.continuation_policy.max_tool_calls,
        },
        "approvals": {
            "source": "focus_transcript",
            "ceiling": 8,
            "observed_action_classes": approval_classes,
            "decision": "session",
        },
        "process_boundary": process_boundary,
        "identities": {
            "run_id": run.run_id,
            "task_id": run.task_id,
            "project_run_id": report.project_run.project_run_id,
            "goal_id": run.goal_id,
            "session_id": run.session_id,
        },
        "tool_calls": calls,
        "plan": plan,
        "plan_revision_count": report.metrics.plan_revision_count,
        "child_state": child_state,
        "verification": [item.model_dump(mode="json") for item in report.verification],
        "verification_state": report.project_run.verification_state.value,
        "independent_verification": verification_result,
        "git_diff": _git(workspace, "diff", "HEAD", "--"),
        "proof_refs": list(report.proof_refs),
        "terminal_state": run.status.value,
        "elapsed_ms": elapsed_ms,
        "tokens": {
            "scope": "focus_parent_session",
            "observed": tokens,
            "project_and_child_total": None,
        },
        "unplanned_interventions": 0,
        "disposition": "pass",
    }


def _run_live_scenario(
    focus_probe: FocusProbe,
    tmp_path: Path,
    scenario_id: str,
) -> None:
    require_complex_focus()
    source_revision = _source_revision()
    root = Path(os.environ.get(_ARTIFACT_ENV, artifact_root(tmp_path))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace, fixture_evidence = _fixture(root, scenario_id)
    probe = focus_probe.for_workdir(workspace)
    home_root = probe.data_root.parent / "home-roots" / probe.session_id
    evidence_path = root / f"{scenario_id}-evidence.json"
    started = time.monotonic()
    assert (
        daemon_start(
            str(probe.config_path), home_root=home_root, data_root=probe.data_root
        )
        == 0
    )
    process_boundary = None
    try:
        first_event_id = _telemetry_cursor(probe)
        with probe.session(rows=52, cols=180) as session:
            probe.wait_ready(session)
            transcript = probe.run_turn(
                session,
                FocusScenario(
                    scenario_id=scenario_id,
                    prompt=LIVE_SCENARIOS[scenario_id],
                    expected_markers=("Project queued:",),
                    requires_approval=True,
                    max_auto_approvals=8,
                    approval_reply="session",
                    timeout=1200,
                ),
            )
            write_transcript(root, scenario_id, transcript)
            approval_classes = _approval_action_classes(transcript)
            assert approval_classes == ["project.start"]
            run_match = _RUN_ID_RE.search(transcript)
            assert run_match is not None, "typed project handoff did not queue a run"
            run_id = run_match.group(1)
            if scenario_id == "plain-multifile-repair":
                before_run, before = _wait_for_run(
                    probe,
                    run_id,
                    lambda run, checkpoint: (
                        checkpoint.project_run.committed_cycle_count == 1
                        and checkpoint.project_run.verification_state
                        == ProjectVerificationState.FAILED
                    ),
                )
                pause = probe.run_slash_turn(
                    session,
                    f"/project pause {run_id}",
                    marker=r"task_state: paused",
                    timeout=120,
                )
                assert before_run.task_id in pause
                accepted_step_value = (workspace / "accepted_steps.log").read_text(
                    encoding="utf-8"
                )
                assert accepted_step_value == "total-implemented\n"
                accepted_step_mtime_ns = (
                    (workspace / "accepted_steps.log").stat().st_mtime_ns
                )
                daemon_before = build_daemon_status_payload(
                    str(probe.config_path),
                    home_root=home_root,
                    data_root=probe.data_root,
                )
                assert (
                    daemon_stop(
                        str(probe.config_path),
                        home_root=home_root,
                        data_root=probe.data_root,
                    )
                    == 0
                )
                daemon_stopped = build_daemon_status_payload(
                    str(probe.config_path),
                    home_root=home_root,
                    data_root=probe.data_root,
                )
                assert daemon_stopped["lifecycle"] == "stopped"
                assert (
                    daemon_start(
                        str(probe.config_path),
                        home_root=home_root,
                        data_root=probe.data_root,
                    )
                    == 0
                )
                daemon_after = build_daemon_status_payload(
                    str(probe.config_path),
                    home_root=home_root,
                    data_root=probe.data_root,
                )
                assert daemon_before["pid"] != daemon_after["pid"]
                resume = probe.run_slash_turn(
                    session,
                    f"/project resume {run_id}",
                    marker=r"task_state: active|status: running",
                    timeout=120,
                )
                assert run_id in resume
                process_boundary = {
                    "stop_point": "after persisted cycle 1 verifier failure",
                    "cycles_before_restart": 1,
                    "verification_before_restart": "failed",
                    "run_id_before_restart": before_run.run_id,
                    "task_id_before_restart": before_run.task_id,
                    "checkpoint_before_restart": before.checkpoint_id,
                    "project_run_id_before_restart": (
                        before.project_run.project_run_id
                    ),
                    "session_id_before_restart": before_run.session_id,
                    "goal_id_before_restart": before_run.goal_id,
                    "daemon_pid_before": daemon_before["pid"],
                    "daemon_pid_after": daemon_after["pid"],
                    "completed_step_ids_before_restart": [
                        step["step_id"]
                        for step in before.payload["task_plan"]["steps"]
                        if step["status"] == "completed"
                    ],
                    "accepted_step_value_before_restart": accepted_step_value,
                    "accepted_step_sha256_before_restart": _digest(accepted_step_value),
                    "accepted_step_mtime_ns_before_restart": (accepted_step_mtime_ns),
                }
        run, checkpoint = _wait_for_run(
            probe,
            run_id,
            lambda run, checkpoint: run.status == AutonomyRunStatus.COMPLETED,
        )
        assert (
            checkpoint.project_run.verification_state
            == ProjectVerificationState.VERIFIED
        )
        assert run.session_id == probe.session_id
        assert run.execution_selectors.verification_commands == ("python -m pytest -q",)
        verification = subprocess.run(
            [str(probe.python_bin), "-m", "pytest", "-q"],
            cwd=workspace,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        assert verification.returncode == 0, verification.stdout + verification.stderr
        status_lines = _git(
            workspace, "status", "--porcelain=v1", "--untracked-files=all"
        ).splitlines()
        assert all(line[:2] in {" M", "M ", "MM"} for line in status_lines)
        changed_paths = sorted(line[3:] for line in status_lines)
        assert changed_paths == fixture_evidence["expected_changed_paths"]
        evidence = _scenario_evidence(
            probe,
            workspace,
            scenario_id,
            fixture_evidence,
            run_id,
            round((time.monotonic() - started) * 1000),
            first_event_id,
            {
                "command": f"{probe.python_bin} -m pytest -q",
                "exit_code": verification.returncode,
                "stdout": verification.stdout,
                "stderr": verification.stderr,
            },
            approval_classes,
            process_boundary=process_boundary,
        )
        assert evidence["source_revision"] == source_revision
        assert evidence["profile"] == "minimax-m2-7"
        assert evidence["model"] == "MiniMax-M2.7"
        assert evidence["configured_act_profile"] == "coding"
        assert evidence["tokens"]["observed"] > 0
        expected_budget = {
            "plain-multifile-repair": (4, 24),
            "research-then-code": (3, 20),
            "delegated-read-only-review": (4, 28),
        }[scenario_id]
        assert evidence["budgets"]["iterations"] == expected_budget[0]
        assert evidence["budgets"]["tool_calls"] == expected_budget[1]
        assert evidence["plan"] is not None
        assert evidence["plan"]["status"] == "completed"
        assert evidence["plan"]["criterion_ids"]
        assert all(step["status"] == "completed" for step in evidence["plan"]["steps"])
        assert evidence["verification"]
        assert len(evidence["proof_refs"]) == 3
        lifecycle = checkpoint.payload["repository_lifecycle"]
        assert set(evidence["proof_refs"]) <= lifecycle.keys()
        objective = lifecycle[checkpoint.project_run.objective_ledger_ref]
        assert objective["success_criteria"]
        assert set(evidence["plan"]["criterion_ids"]) == set(objective["criterion_ids"])
        assert evidence["git_diff"]
        if process_boundary is not None:
            assert (
                evidence["identities"]["task_id"]
                == process_boundary["task_id_before_restart"]
            )
            assert run.run_id == process_boundary["run_id_before_restart"]
            assert run.goal_id == process_boundary["goal_id_before_restart"]
            assert checkpoint.project_run.committed_cycle_count >= 2
            assert (
                checkpoint.checkpoint_id
                != process_boundary["checkpoint_before_restart"]
            )
            assert (
                checkpoint.project_run.project_run_id
                == process_boundary["project_run_id_before_restart"]
            )
            assert run.session_id == process_boundary["session_id_before_restart"]
            completed_after = {
                step["step_id"]
                for step in evidence["plan"]["steps"]
                if step["status"] == "completed"
            }
            completed_before = set(
                process_boundary["completed_step_ids_before_restart"]
            )
            assert completed_before
            assert completed_before <= completed_after
            assert (workspace / "accepted_steps.log").read_text(
                encoding="utf-8"
            ) == process_boundary["accepted_step_value_before_restart"]
            assert (
                _digest((workspace / "accepted_steps.log").read_text(encoding="utf-8"))
                == process_boundary["accepted_step_sha256_before_restart"]
            )
            assert (workspace / "accepted_steps.log").stat().st_mtime_ns == (
                process_boundary["accepted_step_mtime_ns_before_restart"]
            )
            assert evidence["plan_revision_count"] >= 1
        if scenario_id == "research-then-code":
            _assert_required_completed_tools(
                evidence["tool_calls"], {"web.search", "web.fetch"}
            )
            assert _PYPA_GUIDE_URL in (workspace / "source_info.py").read_text(
                encoding="utf-8"
            )
        if scenario_id == "delegated-read-only-review":
            modes = [
                item["mode"]
                for item in evidence["tool_calls"]
                if item["tool"] == "task.delegate"
            ]
            assert "review" in modes
            assert "accept" in modes
            _assert_completed_child_lifecycle(
                evidence["child_state"], parent_agent_id=probe.agent_id
            )
            child_by_mode = {item["mode"]: item for item in evidence["child_state"]}
            assert child_by_mode["sync"]["child_agent_id"] == ("minimax-m2-7-highspeed")
            assert child_by_mode["review"]["child_agent_id"] == (
                "minimax-m2-5-highspeed"
            )
            assert {
                child_by_mode["sync"]["child_agent_id"],
                child_by_mode["review"]["child_agent_id"],
                probe.agent_id,
            } == {
                "minimax-m2-7-highspeed",
                "minimax-m2-5-highspeed",
                "minimax-m2-7",
            }
        evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    finally:
        daemon_stop(
            str(probe.config_path), home_root=home_root, data_root=probe.data_root
        )


@pytest.mark.parametrize("scenario_id", tuple(LIVE_SCENARIOS))
def test_live_minimax_simple_input_project_corpus(
    focus_probe: FocusProbe,
    tmp_path: Path,
    scenario_id: str,
) -> None:
    _run_live_scenario(focus_probe, tmp_path, scenario_id)
