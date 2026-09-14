from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
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


def _source_revision() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[4],
        text=True,
    ).strip()


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
    assert "file.write(" in transcript
    assert "exec.run(" in transcript
    (root / "mnte-focus-live-evidence.json").write_text(
        json.dumps(
            {
                "source_revision": _source_revision(),
                "profile": os.environ["OPENMINION_CLI_FOCUS_E2E_AGENT"],
                "tool_sequence": ["file.write", "exec.run"],
                "verification": "pass",
                "unplanned_interventions": 0,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
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


def test_live_minimax_approved_project_research_code_git_and_denial(
    minimax_agent_id: str,
) -> None:
    require_complex_focus()
    config_path = Path(os.environ["OPENMINION_CLI_FOCUS_E2E_CONFIG"]).expanduser()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["default_agent"] == minimax_agent_id == "minimax-m2-7"
    agent_config = config["agents"][minimax_agent_id]
    provider = str(agent_config["provider"])
    provider_config = {
        **config["providers"][provider],
        **agent_config.get("provider_config_overrides", {}),
    }
    model = str(provider_config["model"])
    assert model == "MiniMax-M2.7"
    root = Path(os.environ["OPENMINION_MNTE_E2E_ARTIFACT_ROOT"]).expanduser()
    workspace = _project_workspace(root)
    artifact_id = f"mnte-project-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    prompts = (
        (
            "Request and use web.search with the query `official PyPA writing "
            "pyproject.toml guide`. Then request and use web.fetch on exactly "
            "https://packaging.python.org/en/latest/guides/writing-pyproject-toml/. "
            "Do not call any other tool. Finish with `result:` and that URL."
        ),
        (
            "Use file.write to replace source_info.py with exactly "
            "`SOURCE_URL = \"https://packaging.python.org/en/latest/guides/"
            "writing-pyproject-toml/\"` followed by a newline. Then use exec.run for "
            "exactly `python -m pytest -q`. Do not call any other tool. Finish with "
            "`result:` and the passing test count."
        ),
        (
            "Request and use git.status to inspect the project change. Then request "
            "the exact release-only tool github.dispatch_workflow. This project has "
            "no release grant, so report its typed unavailable result without "
            "substituting or retrying. Do not call any other tool. Finish with "
            "`result:` and the changed path."
        ),
    )
    runtime = APIRuntime.from_config_path(
        str(config_path),
        home_root=Path(__file__).resolve().parents[4],
        data_root=root / "data" / artifact_id,
    )
    try:
        manager = runtime.resolve_agent_service(minimax_agent_id)._get_runner().task_manager
        store = AutonomyRunStore(root=root / "autonomy")
        launched = launch_project(
            build_project_launch_request(
                goal="Research, implement, verify, inspect Git, and prove release denial",
                session_id=artifact_id,
                agent_id=minimax_agent_id,
                workspace_boundary=workspace,
                repository=workspace,
                require_git_repository=True,
                max_iterations=len(prompts),
                max_tool_calls=20,
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
        payloads = []
        wall_times_ms = []
        for index, prompt in enumerate(prompts, start=1):
            request = ProjectTurnRequest(
                run_id=launched.run_id,
                project_run_id=f"prun_{launched.run_id}",
                task_id=launched.task_id,
                goal_id=str(launched.goal_id),
                session_id=launched.session_id,
                cycle_id=f"cycle-{launched.run_id}-{index}",
                milestone=prompt,
                prompt=prompt,
                allowed_tools=allowed_tools,
                project_tool_calls_remaining=20,
            )
            started = time.monotonic()
            payloads.append(
                runtime.run_turn(
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
                        ),
                    }
                )
            )
            wall_times_ms.append(round((time.monotonic() - started) * 1000))
    finally:
        runtime.close()

    metadata_by_turn = [_result_metadata(payload) for payload in payloads]
    tool_results = [
        result
        for metadata in metadata_by_turn
        for result in parse_tool_results(
            metadata.get("tool_calls_cumulative", metadata.get("tool_results"))
        )
    ]
    tool_names = [str(item.get("tool_name", "")) for item in tool_results]
    expected_tools = {"web.search", "web.fetch", "file.write", "exec.run", "git.status"}
    assert expected_tools <= set(tool_names), tool_results
    assert "github.dispatch_workflow" not in tool_names
    assert any(
        item.get("tool_name") == TOOL_REQUEST_TOOL_NAME
        and item.get("error_code") == "TOOL_REQUEST_UNAVAILABLE"
        for item in tool_results
    ), tool_results
    requested = [
        name
        for metadata in metadata_by_turn
        for name in metadata.get("tool_schema_shortlisting.requested_tools", [])
    ]
    assert "web.fetch" in requested
    assert "git.status" in requested
    assert "github.dispatch_workflow" in requested
    assert all(
        int(metadata["tool_schema_shortlisting.candidate_count"]) > 7
        for metadata in metadata_by_turn
    )
    assert all(
        int(metadata["tool_schema_shortlisting.initial_active_count"]) <= 7
        and int(metadata["tool_schema_shortlisting.max_active_count"])
        <= int(metadata["tool_schema_shortlisting.candidate_count"])
        and int(metadata["tool_schema_shortlisting.control_schema_count"]) <= 2
        for metadata in metadata_by_turn
    )
    response_text = "\n".join(str(payload.get("body", "")) for payload in payloads)
    assert "TOOL_REQUEST_UNAVAILABLE" in response_text
    assert all("result:" in str(payload.get("body", "")).lower() for payload in payloads)

    assert (workspace / "source_info.py").read_text(encoding="utf-8") == (
        'SOURCE_URL = "https://packaging.python.org/en/latest/guides/'
        'writing-pyproject-toml/"\n'
    )
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

    telemetry_path = root / "data" / artifact_id / "telemetry" / "telemetry.db"
    with sqlite3.connect(telemetry_path) as connection:
        timing_rows = connection.execute(
            "SELECT data FROM events WHERE event_type = 'chat.phase_timing' ORDER BY id"
        ).fetchall()
        schema_rows = connection.execute(
            "SELECT data FROM events WHERE event_type = 'llm.call.completed' ORDER BY id"
        ).fetchall()
    phase_timing = [json.loads(row[0]) for row in timing_rows]
    provider_attempts = [
        attempt
        for event in phase_timing
        for attempt in event.get("provider_attempts", [])
    ]
    schema_calls = [
        event
        for row in schema_rows
        if (event := json.loads(row[0])).get("purpose") == "act"
    ]
    assert phase_timing and provider_attempts and schema_calls

    (root / "mnte-project-live-evidence.json").write_text(
        json.dumps(
            {
                "source_revision": _source_revision(),
                "run_id": launched.run_id,
                "workspace": str(workspace),
                "profile": minimax_agent_id,
                "provider": provider,
                "model": model,
                "tool_names": tool_names,
                "requested_tools": requested,
                "schema_counts": [
                    {
                        "candidate": metadata.get("tool_schema_shortlisting.candidate_count"),
                        "initial_execution": metadata.get(
                            "tool_schema_shortlisting.initial_active_count"
                        ),
                        "max_execution": metadata.get(
                            "tool_schema_shortlisting.max_active_count"
                        ),
                        "controls": metadata.get(
                            "tool_schema_shortlisting.control_schema_count"
                        ),
                    }
                    for metadata in metadata_by_turn
                ],
                "inactive_directory_sequence": [
                    {
                        "count": metadata.get(
                            "tool_schema_shortlisting.inactive_directory_count"
                        ),
                        "bytes": metadata.get(
                            "tool_schema_shortlisting.inactive_directory_bytes"
                        ),
                    }
                    for metadata in metadata_by_turn
                ],
                "provider_calls": sum(
                    int(event.get("provider_calls_total", 0) or 0)
                    for event in phase_timing
                ),
                "provider_attempts": provider_attempts,
                "compatibility_retries": sum(
                    int(attempt.get("attempt", 1) or 1) > 1
                    for attempt in provider_attempts
                ),
                "tool_schema_calls": [
                    {
                        "count": event.get("tool_schema_count"),
                        "bytes": event.get("tool_schema_bytes"),
                        "retry_count": event.get("retry_count"),
                    }
                    for event in schema_calls
                ],
                "input_tokens": sum(
                    int(metadata.get("total_input_tokens_used", 0) or 0)
                    for metadata in metadata_by_turn
                ),
                "output_tokens": sum(
                    int(metadata.get("total_output_tokens_used", 0) or 0)
                    for metadata in metadata_by_turn
                ),
                "wall_times_ms": wall_times_ms,
                "termination_reasons": [
                    metadata.get("coding.termination_reason")
                    for metadata in metadata_by_turn
                ],
                "git_status": git_status,
                "verification": "pass",
                "unplanned_interventions": 0,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
