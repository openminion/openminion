from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import pytest

from openminion.api.runtime import APIRuntime
from openminion.cli.commands.autonomy_project import (
    build_project_launch_request,
    launch_project,
)
from openminion.modules.brain.loop.strategies.coding.contracts import (
    PROJECT_CODING_ALLOWED_TOOLS,
)
from openminion.modules.brain.loop.tools.shortlisting import TOOL_REQUEST_TOOL_NAME
from openminion.modules.task.autonomy import (
    AutonomyRunStore,
    autonomy_permission_metadata,
)
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
    (workspace / "test_slug.py").write_text(
        "from slug import slugify\n\n"
        "def test_slugify() -> None:\n"
        "    assert slugify('Hello, Open Minion!') == 'hello-open-minion'\n",
        encoding="utf-8",
    )
    scenario = FocusScenario(
        scenario_id="mnte-core-edit-test",
        prompt=(
            "Work only in the current directory. Create `slug.py` with a small "
            "`slugify(text)` function that passes the existing test. Use file.write "
            "for `slug.py` and exec.run for exactly "
            "`python -m pytest -q`. Do not request optional tools. Finish with "
            "the exact label `result:` and the passing test count."
        ),
        expected_markers=("result",),
        requires_approval=True,
        max_auto_approvals=8,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
        expected_file_patterns=("slug.py",),
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
    (workspace / "README.md").write_text("# MNTE fixture\n", encoding="utf-8")
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


def test_live_minimax_approved_project_search_and_release_denial(
    minimax_agent_id: str,
) -> None:
    require_complex_focus()
    config_path = Path(os.environ["OPENMINION_CLI_FOCUS_E2E_CONFIG"]).expanduser()
    root = Path(os.environ["OPENMINION_MNTE_E2E_ARTIFACT_ROOT"]).expanduser()
    workspace = _project_workspace(root)
    artifact_id = f"mnte-project-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    prompt = (
        "This is one bounded approved-project capability check. Request and use "
        "web.search with the query `official PyPA writing pyproject.toml guide` and "
        "at most three results. After that succeeds, request the exact release-only "
        "tool github.dispatch_workflow. This project has no release grant, so report "
        "its typed unavailable result without substituting or retrying. Do not call "
        "plan or any other tool. Finish with `result:` and the PyPA URL."
    )
    runtime = APIRuntime.from_config_path(
        str(config_path),
        home_root=Path(__file__).resolve().parents[4],
        data_root=root / "data" / artifact_id,
    )
    try:
        service = runtime.resolve_agent_service(minimax_agent_id)
        manager = service._get_runner().task_manager
        store = AutonomyRunStore(root=root / "autonomy")
        launched = launch_project(
            build_project_launch_request(
                goal=prompt,
                session_id=artifact_id,
                agent_id=minimax_agent_id,
                workspace_boundary=workspace,
                repository=workspace,
                require_git_repository=True,
                max_iterations=1,
                max_tool_calls=10,
                permission_profile_id="bypass",
                config_ref=str(config_path),
                verification_domain="coding",
                verification_commands=("git status --short",),
            ),
            store=store,
            manager=manager,
        )
        assert launched.task_id is not None
        allowed_tools = tuple(
            sorted({*PROJECT_CODING_ALLOWED_TOOLS, TOOL_REQUEST_TOOL_NAME})
        )
        request = ProjectTurnRequest(
            run_id=launched.run_id,
            project_run_id=f"prun_{launched.run_id}",
            task_id=launched.task_id,
            goal_id=str(launched.goal_id),
            session_id=launched.session_id,
            cycle_id=f"cycle-{launched.run_id}-capability",
            milestone=prompt,
            prompt=prompt,
            allowed_tools=allowed_tools,
            project_tool_calls_remaining=10,
        )
        payload = runtime.run_turn(
            payload={
                "message": prompt,
                "agent_id": minimax_agent_id,
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
                )
            }
        )
    finally:
        runtime.close()

    metadata = _result_metadata(payload)
    tool_results = parse_tool_results(
        metadata.get("tool_calls_cumulative", metadata.get("tool_results"))
    )
    tool_names = [str(item.get("tool_name", "")) for item in tool_results]
    assert "web.search" in tool_names, tool_results
    assert "github.dispatch_workflow" not in tool_names
    requested = metadata.get("tool_schema_shortlisting.requested_tools", [])
    assert "github.dispatch_workflow" in requested
    assert int(metadata["tool_schema_shortlisting.candidate_count"]) > 7
    selected = metadata["tool_schema_shortlisting.selected_tools"]
    assert "web.search" in selected
    assert len(selected) <= 7
    response_body = str(payload.get("body", ""))
    assert "TOOL_REQUEST_UNAVAILABLE" in response_body
    assert "result:" in response_body.lower()

    (root / "mnte-project-live-evidence.json").write_text(
        json.dumps(
            {
                "run_id": launched.run_id,
                "workspace": str(workspace),
                "tool_names": tool_names,
                "requested_tools": requested,
                "candidate_count": metadata.get(
                    "tool_schema_shortlisting.candidate_count"
                ),
                "active_count": metadata.get(
                    "tool_schema_shortlisting.active_count"
                ),
                "llm_calls": metadata.get("coding.llm_calls"),
                "total_tokens": metadata.get("total_tokens_used"),
                "termination_reason": metadata.get("coding.termination_reason"),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
