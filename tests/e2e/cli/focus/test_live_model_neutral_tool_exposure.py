from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import pytest

from openminion.api.runtime import APIRuntime
from openminion.modules.brain.loop.strategies.coding.contracts import (
    PROJECT_CODING_ALLOWED_TOOLS,
)
from openminion.modules.brain.loop.tools.shortlisting import TOOL_REQUEST_TOOL_NAME
from openminion.modules.task.autonomy import autonomy_permission_metadata
from openminion.modules.task.project import ProjectTurnRequest, project_turn_inbound_metadata
from tests.e2e.cli.focus.conftest import require_complex_focus
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.scenarios import FocusScenario, assert_scenario_contract
from tests.helpers.live_cli_chat_alibaba import parse_tool_results

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(1500)]


def test_live_focus_core_edit_and_test_uses_bounded_tools(
    focus_probe: FocusProbe,
    tmp_path: Path,
) -> None:
    require_complex_focus()
    root = artifact_root(tmp_path)
    workspace = root / "scratch" / "mnte-core-edit-test"
    workspace.mkdir(parents=True)
    scenario = FocusScenario(
        scenario_id="mnte-core-edit-test",
        prompt=(
            "Work only in the current directory. Create `slug.py` with a small "
            "`slugify(text)` function and `test_slug.py` with two pytest cases. "
            "Use file.write for both files and exec.run for exactly "
            "`python -m pytest -q`. Do not request optional tools. Finish with "
            "the exact label `result:` and the passing test count."
        ),
        expected_markers=("result",),
        requires_approval=True,
        max_auto_approvals=8,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
        expected_file_patterns=("slug.py", "test_slug.py"),
        validation_commands=(("{python}", "-m", "pytest", "-q"),),
        max_auto_continuations=2,
    )
    probe = focus_probe.for_workdir(workspace, include_project_context=False)

    with probe.session(rows=50, cols=160) as session:
        probe.wait_ready(session)
        try:
            transcript = probe.run_turn(session, scenario)
        except BaseException:
            write_transcript(root, scenario.scenario_id, session.transcript)
            raise
        write_transcript(root, scenario.scenario_id, transcript)

    assert_scenario_contract(
        scenario,
        scratch_dir=workspace,
        transcript=transcript,
        python_bin=focus_probe.python_bin,
    )


def _project_workspace(root: Path) -> Path:
    workspace = root / "workspaces" / f"mnte-project-{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True)
    (workspace / "source_info.py").write_text('SOURCE_URL = ""\n', encoding="utf-8")
    (workspace / "test_source_info.py").write_text(
        "from source_info import SOURCE_URL\n\n"
        "def test_source_url() -> None:\n"
        "    assert SOURCE_URL == "
        "'https://packaging.python.org/en/latest/guides/writing-pyproject-toml/'\n",
        encoding="utf-8",
    )
    (workspace / ".gitignore").write_text(
        ".pytest_cache/\n__pycache__/\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.email", "mnte@example.invalid"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "MNTE Fixture"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed fixture"],
        cwd=workspace,
        check=True,
    )
    return workspace


def _result_metadata(payload: dict[str, object]) -> dict[str, object]:
    raw = payload.get("metadata")
    return dict(raw) if isinstance(raw, dict) else {}


def test_live_minimax_approved_project_research_code_git_and_denial() -> None:
    require_complex_focus()
    config_path = Path(os.environ["OPENMINION_CLI_FOCUS_E2E_CONFIG"]).expanduser()
    root = Path(os.environ["OPENMINION_MNTE_E2E_ARTIFACT_ROOT"]).expanduser()
    workspace = _project_workspace(root)
    run_id = f"mnte-project-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    allowed_tools = tuple(
        sorted({*PROJECT_CODING_ALLOWED_TOOLS, TOOL_REQUEST_TOOL_NAME})
    )
    request = ProjectTurnRequest(
        run_id=run_id,
        project_run_id=f"project-{run_id}",
        task_id=f"task-{run_id}",
        goal_id=f"goal-{run_id}",
        session_id=run_id,
        cycle_id=f"cycle-{run_id}",
        milestone="Research, implement, verify, and inspect the local change",
        prompt="",
        allowed_tools=allowed_tools,
        project_tool_calls_remaining=20,
    )
    prompt = (
        "Work only in the supplied project workspace. Use the plan control for a "
        "short plan, then exercise progressive tool activation exactly as needed. "
        "First request and use web.search to find the official PyPA guide for "
        "writing pyproject.toml. Next request and use web.fetch on "
        "https://packaging.python.org/en/latest/guides/writing-pyproject-toml/. "
        "Then rewrite source_info.py so SOURCE_URL equals that exact URL, and use "
        "exec.run to run exactly `python -m pytest -q`. Request and use git.status "
        "to inspect the local change; do not edit any other file, create another "
        "file, or commit. Finally request the exact "
        "release-only tool github.dispatch_workflow. This project has no release "
        "grant, so record its typed unavailable result and do not substitute, retry, "
        "or call any release tool. Finish with `result:` and summarize the verified "
        "change and the denied release request."
    )
    runtime = APIRuntime.from_config_path(
        str(config_path),
        home_root=Path(__file__).resolve().parents[4],
        data_root=root / "data" / run_id,
    )
    try:
        payload = runtime.run_turn(
            payload={
                "message": prompt,
                "agent": "minimax-m2-7",
                "session_id": request.session_id,
                "channel": "console",
                "target": "mnte-project",
                "timeout_seconds": 1200,
                "inbound_metadata": project_turn_inbound_metadata(
                    request,
                    base={
                        "source": "openminion.mnte.e2e",
                        "workspace_root": str(workspace),
                        **autonomy_permission_metadata("bypass"),
                    },
                ),
            }
        )
    finally:
        runtime.close()

    metadata = _result_metadata(payload)
    tool_results = parse_tool_results(
        metadata.get("tool_calls_cumulative", metadata.get("tool_results"))
    )
    tool_names = [str(item.get("tool_name", "")) for item in tool_results]
    expected_executed = {"web.search", "web.fetch", "file.write", "exec.run", "git.status"}
    assert expected_executed <= set(tool_names), tool_results
    assert "github.dispatch_workflow" not in tool_names
    assert any(
        item.get("tool_name") == TOOL_REQUEST_TOOL_NAME
        and item.get("error_code") == "TOOL_REQUEST_UNAVAILABLE"
        for item in tool_results
    ), tool_results
    requested = metadata.get("tool_schema_shortlisting.requested_tools", [])
    assert "web.search" in requested
    assert "web.fetch" in requested
    assert "git.status" in requested
    assert "github.dispatch_workflow" in requested
    assert int(metadata["tool_schema_shortlisting.candidate_count"]) > 7
    assert len(metadata["tool_schema_shortlisting.selected_tools"]) <= 7

    verification = subprocess.run(
        [str(Path(__file__).resolve().parents[4] / ".venv/bin/python3.11"), "-m", "pytest", "-q"],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )
    assert verification.returncode == 0, verification.stdout + verification.stderr
    git_status = subprocess.run(
        ["git", "status", "--short"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert git_status == [" M source_info.py"]

    (root / "mnte-project-live-evidence.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "workspace": str(workspace),
                "tool_names": tool_names,
                "requested_tools": requested,
                "candidate_count": metadata.get(
                    "tool_schema_shortlisting.candidate_count"
                ),
                "active_count": metadata.get("tool_schema_shortlisting.active_count"),
                "llm_calls": metadata.get("coding.llm_calls"),
                "input_tokens": metadata.get("total_input_tokens_used"),
                "output_tokens": metadata.get("total_output_tokens_used"),
                "total_tokens": metadata.get("total_tokens_used"),
                "termination_reason": metadata.get("coding.termination_reason"),
                "verification": "pass",
                "git_status": git_status,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
