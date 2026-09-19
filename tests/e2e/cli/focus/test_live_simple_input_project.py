from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import time

import pytest

from openminion.modules.task import AutonomyRunStore, TaskManager
from openminion.modules.task.autonomy import AutonomyRunStatus
from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
from openminion.modules.task.project import load_latest_project_checkpoint
from tests.e2e.cli.focus.conftest import require_complex_focus
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(1800)]

_RUN_ID_RE = re.compile(r"Project queued:\s*(awrk_[A-Za-z0-9_]+)")
_PYTHON = shlex.quote(sys.executable)
_SCENARIOS = (
    (
        "plain-restart-repair",
        "This is durable project work. Before using any execution tool, propose a "
        "project handoff. After approval, fix calculator.py and formatting.py so "
        f"all tests pass. Use the verification command exactly `{_PYTHON} "
        "verify_once.py`, a "
        "maximum of 4 iterations, and measurable success criteria. The verifier "
        "intentionally fails its first invocation; revise the task plan from that "
        "evidence, repair if needed, and finish only after verification passes.",
    ),
    (
        "research-then-code",
        "This is durable project work. Before using any execution or research tool, "
        "propose a project handoff. After approval, search for and fetch the current "
        "official PyPA guide for writing "
        "pyproject.toml before choosing the implementation. Update source_info.py "
        "and source_summary.md with the authoritative URL and a concise finding. "
        f"Use `{_PYTHON} -m pytest -q` as the verification command, at most 4 "
        "iterations, and finish only after the tests pass.",
    ),
    (
        "delegated-review",
        "This is durable project work. Before using any execution tool, propose a "
        "project handoff. After approval, implement the requested changes in "
        f"feature.py and CHANGELOG.md, run `{_PYTHON} -m pytest -q`, then delegate "
        "one independent read-only review "
        "to one exact agent_id returned by agent.list and explicitly accept, reject, "
        "or reassign its findings before completion. Use at most 4 iterations and "
        "measurable success criteria.",
    ),
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _fixture(repo: Path, scenario_id: str) -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "silc-live@example.invalid")
    _git(repo, "config", "user.name", "SILC Live")
    if scenario_id == "plain-restart-repair":
        (repo / "calculator.py").write_text(
            "def add(left, right):\n    return 0\n", encoding="utf-8"
        )
        (repo / "formatting.py").write_text(
            "def title(text):\n    return text\n", encoding="utf-8"
        )
        (repo / "test_project.py").write_text(
            "from calculator import add\nfrom formatting import title\n\n"
            "def test_project():\n"
            "    assert add(2, 3) == 5\n"
            "    assert title('open minion') == 'Open Minion'\n",
            encoding="utf-8",
        )
        (repo / "verify_once.py").write_text(
            "from pathlib import Path\nimport subprocess\nimport sys\n\n"
            "marker = Path('.verification-seen')\n"
            "if not marker.exists():\n"
            "    marker.write_text('seen\\n')\n"
            "    raise SystemExit(1)\n"
            "raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-q']))\n",
            encoding="utf-8",
        )
    elif scenario_id == "research-then-code":
        (repo / "source_info.py").write_text("SOURCE_URL = ''\n", encoding="utf-8")
        (repo / "source_summary.md").write_text(
            "# Source\n\nPending.\n", encoding="utf-8"
        )
        (repo / "test_source.py").write_text(
            "from source_info import SOURCE_URL\n\n"
            "def test_source():\n"
            "    assert SOURCE_URL == "
            "'https://packaging.python.org/en/latest/guides/writing-pyproject-toml/'\n",
            encoding="utf-8",
        )
    else:
        (repo / "feature.py").write_text("VALUE = 0\n", encoding="utf-8")
        (repo / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
        (repo / "test_feature.py").write_text(
            "from feature import VALUE\n\ndef test_feature():\n    assert VALUE == 2\n",
            encoding="utf-8",
        )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "seed SILC live fixture")


def _resume(probe: FocusProbe, run_id: str, *, iterations: int) -> dict:
    command = [
        str(probe.python_bin),
        "-m",
        "openminion",
        "--config",
        str(probe.config_path),
        "--home-root",
        probe.environment()["OPENMINION_HOME"],
        "--data-root",
        str(probe.data_root),
        "autonomy",
        "resume",
        run_id,
        "--agent",
        probe.agent_id,
        "--max-iterations",
        str(iterations),
        "--json",
    ]
    completed = subprocess.run(
        command,
        cwd=probe.openminion_root,
        env={**os.environ, **probe.environment()},
        capture_output=True,
        text=True,
        timeout=1200,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout)


def _telemetry_evidence(data_root: Path) -> dict[str, object]:
    database = data_root / "telemetry" / "telemetry.db"
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT event_type, data FROM events "
            "WHERE event_type IN "
            "('tool.call.requested', 'tool.call.completed', 'chat.phase_timing') "
            "ORDER BY id"
        ).fetchall()
    events = [(str(kind), json.loads(data)) for kind, data in rows]
    tool_names = [
        str(event.get("canonical_name", ""))
        for kind, event in events
        if kind == "tool.call.requested"
    ]
    timings = [event for kind, event in events if kind == "chat.phase_timing"]
    return {
        "tool_sequence": tool_names,
        "delegation_results": [
            event.get("output")
            for kind, event in events
            if kind == "tool.call.completed"
            and event.get("canonical_name") == "task.delegate"
        ],
        "provider_calls": sum(
            int(event.get("provider_calls_total", 0) or 0) for event in timings
        ),
        "input_tokens": sum(
            int(event.get("provider_input_tokens", 0) or 0) for event in timings
        ),
        "output_tokens": sum(
            int(event.get("provider_output_tokens", 0) or 0) for event in timings
        ),
    }


@pytest.mark.parametrize(
    ("scenario_id", "prompt"),
    _SCENARIOS,
    ids=[scenario_id for scenario_id, _ in _SCENARIOS],
)
def test_live_focus_simple_input_project(
    focus_probe: FocusProbe,
    tmp_path: Path,
    scenario_id: str,
    prompt: str,
) -> None:
    require_complex_focus()
    source_revision = _git(focus_probe.openminion_root, "rev-parse", "HEAD")
    config_bytes = focus_probe.config_path.read_bytes()
    config = json.loads(config_bytes)
    assert config["module_configs"]["brain"]["request_handoff"]["enabled"] is True
    profile = config["agents"][focus_probe.agent_id]
    provider_overrides = profile.get("provider_config_overrides", {})
    root = artifact_root(tmp_path)
    repo = root / "fixtures" / scenario_id
    _fixture(repo, scenario_id)
    probe = focus_probe.for_workdir(repo)
    evidence_path = root / f"silc-{scenario_id}-evidence.json"
    started = time.monotonic()
    evidence: dict[str, object] = {
        "scenario_id": scenario_id,
        "source_revision_start": source_revision,
        "profile": probe.agent_id,
        "provider": profile.get("provider"),
        "model": provider_overrides.get("model"),
        "config_sha256": sha256(config_bytes).hexdigest(),
        "prompt_sha256": sha256(prompt.encode()).hexdigest(),
        "fixture_revision": _git(repo, "rev-parse", "HEAD"),
        "disposition": "failed",
        "unplanned_interventions": 0,
    }
    try:
        scenario = FocusScenario(
            scenario_id=scenario_id,
            prompt=prompt,
            expected_markers=("Project queued:",),
            requires_approval=True,
            approval_reply="yes",
            max_auto_approvals=2,
            timeout=300,
        )
        with probe.session(rows=50, cols=160) as session:
            probe.wait_ready(session)
            probe.run_slash(session, "/permissions bypass", marker="bypass")
            try:
                transcript = probe.run_turn(session, scenario)
            finally:
                transcript_path = write_transcript(
                    root, f"silc-{scenario_id}", session.visible_transcript
                )
        match = _RUN_ID_RE.search(transcript)
        assert match is not None, transcript[-2000:]
        run_id = match.group(1)
        first = _resume(
            probe, run_id, iterations=1 if scenario_id == "plain-restart-repair" else 4
        )
        final = (
            _resume(probe, run_id, iterations=4)
            if scenario_id == "plain-restart-repair"
            else first
        )
        store = AutonomyRunStore(
            root=(
                Path(probe.environment()["OPENMINION_GENERATED_ROOT"])
                / "state"
                / "task"
                / "autonomy"
            )
        )
        run = store.require(run_id)
        manager = TaskManager.for_lifecycle_db(
            db_path=probe.data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH
        )
        try:
            checkpoint = load_latest_project_checkpoint(
                manager, task_id=run.task_id or ""
            )
        finally:
            manager.close()
        assert checkpoint is not None
        telemetry = _telemetry_evidence(probe.data_root)
        tools = telemetry["tool_sequence"]
        assert isinstance(tools, list)
        verification = subprocess.run(
            [str(probe.python_bin), "-m", "pytest", "-q"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=120,
        )
        evidence.update(
            {
                "run_id": run_id,
                "task_id": run.task_id,
                "session_id": run.session_id,
                "budgets": run.continuation_policy.model_dump(mode="json"),
                "approved_permission_profile": run.permission_profile_id,
                "project_launch_approvals": 1,
                "process_boundary": "focus_exit_then_autonomy_resume",
                "transcript": transcript_path.name,
                "first_status": first.get("status"),
                "final_status": final.get("status"),
                "task_plan": checkpoint.payload.get("task_plan"),
                "plan_revision_count": checkpoint.payload.get("plan_revision_count", 0),
                **telemetry,
                "git_diff": _git(repo, "diff", "HEAD"),
                "verification": verification.stdout + verification.stderr,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "source_revision_end": _git(probe.openminion_root, "rev-parse", "HEAD"),
            }
        )
        assert run.status == AutonomyRunStatus.COMPLETED
        assert checkpoint.payload.get("task_plan")
        assert verification.returncode == 0, evidence["verification"]
        assert evidence["git_diff"]
        if scenario_id == "plain-restart-repair":
            assert checkpoint.payload.get("plan_revision_count", 0) >= 1
        elif scenario_id == "research-then-code":
            assert "web.search" in tools and "web.fetch" in tools
        else:
            assert "task.delegate" in tools
            assert telemetry["delegation_results"]
        evidence["disposition"] = "pass"
    except Exception as exc:
        evidence["error_code"] = type(exc).__name__
        evidence["error_message"] = str(exc)[-2000:]
        raise
    finally:
        evidence.setdefault(
            "source_revision_end", _git(probe.openminion_root, "rev-parse", "HEAD")
        )
        evidence["revision_stable"] = (
            evidence["source_revision_start"] == evidence["source_revision_end"]
        )
        evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
