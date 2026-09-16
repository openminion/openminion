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
from openminion.modules.task.project import (
    ProjectTurnRequest,
    project_turn_inbound_metadata,
)
from openminion.tools.exec.constants import EXEC_ENABLE_HOST_EXEC_ENV
from tests.e2e.cli.focus.conftest import require_complex_focus
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.scenarios import (
    FocusScenario,
    assert_scenario_contract,
)
from tests.helpers.live_cli_chat_alibaba import parse_tool_results

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(1500)]

_PYPA_GUIDE_URL = (
    "https://packaging.python.org/en/latest/guides/writing-pyproject-toml/"
)


def _source_revision() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[4],
        text=True,
    ).strip()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _telemetry_events(path: Path, event_type: str) -> list[dict[str, object]]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT data FROM events WHERE event_type = ? ORDER BY id",
            (event_type,),
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def _telemetry_event_rows(
    path: Path, event_types: tuple[str, ...]
) -> list[tuple[str, str, dict[str, object]]]:
    placeholders = ", ".join("?" for _ in event_types)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            f"SELECT turn_id, event_type, data FROM events "
            f"WHERE event_type IN ({placeholders}) ORDER BY id",
            event_types,
        ).fetchall()
    return [
        (str(turn_id), str(event_type), json.loads(raw_data))
        for turn_id, event_type, raw_data in rows
    ]


def _agent_identity(config_path: Path, agent_id: str) -> tuple[str, str, str]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    agent_config = config["agents"][agent_id]
    provider = str(agent_config["provider"])
    provider_config = {
        **config["providers"][provider],
        **agent_config.get("provider_config_overrides", {}),
    }
    return (
        provider,
        str(provider_config["model"]),
        str(agent_config.get("default_act_profile", "")),
    )


def _provider_failure_categories(telemetry_path: Path) -> list[str]:
    if not telemetry_path.is_file():
        return []
    with sqlite3.connect(telemetry_path) as connection:
        rows = connection.execute(
            "SELECT event_type, data FROM events "
            "WHERE event_type IN (?, ?, ?) ORDER BY id",
            ("llm.call.failed", "module.stats", "agent.turn.failed"),
        ).fetchall()
    categories = []
    for event_type, raw_data in rows:
        event = json.loads(raw_data)
        if event_type == "llm.call.failed":
            raw_error = event.get("error")
            nested_code = raw_error.get("code") if isinstance(raw_error, dict) else ""
            if category := str(event.get("error_category") or nested_code or ""):
                categories.append(category)
        elif (
            event.get("module_id") == "openminion-llm"
            and event.get("operation") == "error"
        ):
            if category := str(event.get("error_code", "")):
                categories.append(category)
        elif event_type == "agent.turn.failed":
            raw_error = event.get("error")
            if isinstance(raw_error, dict):
                if category := str(raw_error.get("code", "")):
                    categories.append(category)
    return categories


def _failure_disposition(categories: list[str]) -> str:
    if {
        "EMPTY_PAYLOAD",
        "EMPTY_PROVIDER_RESPONSE",
        "PROVIDER_ERROR",
        "RATE_LIMITED",
        "TIMEOUT",
    } & set(categories):
        return "provider_residual"
    return "failed"


def test_live_focus_core_edit_and_test_uses_bounded_tools(
    request: pytest.FixtureRequest,
    focus_probe: FocusProbe,
    tmp_path: Path,
) -> None:
    require_complex_focus()
    source_revision = _source_revision()
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
            "for `slug.py` and exec.run with the command exactly "
            "`python -m pytest -q` and `yield_ms` 30000 so you observe the result. "
            "Do not request optional tools. Finish with "
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
    provider, model, configured_act_profile = _agent_identity(
        probe.config_path, probe.agent_id
    )
    evidence_path = root / "mnte-focus-live-evidence.json"
    telemetry_path = probe.data_root / "telemetry" / "telemetry.db"

    def write_failure_evidence() -> None:
        if evidence_path.exists():
            return
        failure_categories = _provider_failure_categories(telemetry_path)
        _write_json(
            evidence_path,
            {
                "scenario_id": scenario.scenario_id,
                "source_revision_start": source_revision,
                "source_revision_end": _source_revision(),
                "profile": probe.agent_id,
                "configured_act_profile": configured_act_profile,
                "provider": provider,
                "model": model,
                "provider_failure_categories": failure_categories,
                "disposition": _failure_disposition(failure_categories),
                "unplanned_interventions": 0,
            },
        )

    request.addfinalizer(write_failure_evidence)
    assert configured_act_profile == "coding"

    with probe.session(rows=50, cols=160) as session:
        probe.wait_ready(session)
        try:
            transcript = probe.run_turn(session, scenario)
        finally:
            transcript = session.transcript
            write_transcript(root, scenario.scenario_id, transcript)

    source_revision_end = _source_revision()
    assert source_revision_end == source_revision
    assert_scenario_contract(
        scenario,
        scratch_dir=workspace,
        transcript=transcript,
        python_bin=focus_probe.python_bin,
    )
    assert "file.write(" in transcript
    assert "exec.run(" in transcript
    requested_events = _telemetry_events(telemetry_path, "tool.call.requested")
    completed_events = _telemetry_events(telemetry_path, "tool.call.completed")
    status_events = _telemetry_events(telemetry_path, "brain.execution_status")
    bootstrap = _telemetry_events(telemetry_path, "brain.act.bootstrap")[-1]
    assert bootstrap["resolved_act_profile"] == "coding"
    timing = _telemetry_events(telemetry_path, "chat.phase_timing")[-1]
    tool_sequence = [
        str(event.get("canonical_name", ""))
        for event in requested_events
        if event.get("canonical_name") != TOOL_REQUEST_TOOL_NAME
    ]
    activated_tools = [
        str(event.get("sanitized_normalized_arguments", {}).get("name", ""))
        for event in requested_events
        if event.get("canonical_name") == TOOL_REQUEST_TOOL_NAME
    ]
    completed_by_id = {
        str(event.get("call_id", "")): event for event in completed_events
    }
    exec_request = next(
        event for event in requested_events if event.get("canonical_name") == "exec.run"
    )
    exec_output = completed_by_id[str(exec_request["call_id"])]["output"]["outputs"]
    assert exec_output["status"] == "ok"
    assert exec_output["exit_code"] == 0
    assert "1 passed" in str(exec_output.get("stdout", ""))
    shortlisting = next(
        event
        for event in reversed(status_events)
        if "tool_schema_shortlisting.initial_active_count" in event
        and "adaptive.allowed_tools" in event
    )
    _write_json(
        evidence_path,
        {
            "scenario_id": scenario.scenario_id,
            "source_revision_start": source_revision,
            "source_revision_end": source_revision_end,
            "revision_stable": True,
            "profile": probe.agent_id,
            "configured_act_profile": configured_act_profile,
            "provider": provider,
            "model": model,
            "resolved_act_profile": bootstrap["resolved_act_profile"],
            "allowed_ceiling_count": len(shortlisting["adaptive.allowed_tools"]),
            "initial_active_count": shortlisting[
                "tool_schema_shortlisting.initial_active_count"
            ],
            "max_active_count": shortlisting[
                "tool_schema_shortlisting.max_active_count"
            ],
            "control_schema_count": shortlisting[
                "tool_schema_shortlisting.control_schema_count"
            ],
            "inactive_directory_count": shortlisting[
                "tool_schema_shortlisting.inactive_directory_count"
            ],
            "inactive_directory_bytes": shortlisting[
                "tool_schema_shortlisting.inactive_directory_bytes"
            ],
            "activated_tools": activated_tools,
            "tool_sequence": tool_sequence,
            "provider_calls": timing["provider_calls_total"],
            "provider_call_purposes": timing["provider_call_purposes"],
            "provider_attempts": timing["provider_attempts"],
            "input_tokens": timing["provider_input_tokens"],
            "output_tokens": timing["provider_output_tokens"],
            "total_tokens": int(timing["provider_input_tokens"])
            + int(timing["provider_output_tokens"]),
            "wall_time_ms": timing["total_turn_ms"],
            "terminal_result_observed": "result:" in transcript.lower(),
            "verification": "pass",
            "disposition": "pass",
            "unplanned_interventions": 0,
        },
    )


def _project_workspace(root: Path) -> Path:
    workspace = root / "workspaces" / f"mnte-project-{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True)
    (workspace / "source_info.py").write_text('SOURCE_URL = ""\n', encoding="utf-8")
    (workspace / "test_source_info.py").write_text(
        "from source_info import SOURCE_URL\n\n"
        "def test_source_url() -> None:\n"
        f"    assert SOURCE_URL == {_PYPA_GUIDE_URL!r}\n",
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


def _turn_local_tool_results(
    metadata_by_turn: list[dict[str, object]],
) -> list[list[dict]]:
    seen_call_ids: set[str] = set()
    results_by_turn = []
    for metadata in metadata_by_turn:
        current = []
        for item in parse_tool_results(metadata.get("tool_results")):
            call_id = str(item.get("call_id", "")).strip()
            if call_id and call_id in seen_call_ids:
                continue
            if call_id:
                seen_call_ids.add(call_id)
            current.append(item)
        results_by_turn.append(current)
    return results_by_turn


def _fetched_source_url(tool_results: list[dict]) -> str | None:
    for item in tool_results:
        if item.get("tool_name") == "web.fetch" and item.get("ok") is True:
            return str(item["data"]["data"]["final_url"])
    return None


def _scenario_disposition(passed: bool, failure_categories: list[str]) -> str:
    if passed:
        return "pass"
    return _failure_disposition(failure_categories)


def test_live_minimax_approved_project_research_code_git_and_denial(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    minimax_agent_id: str,
) -> None:
    require_complex_focus()
    source_revision = _source_revision()
    config_path = Path(os.environ["OPENMINION_CLI_FOCUS_E2E_CONFIG"]).expanduser()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["default_agent"] == minimax_agent_id
    provider, model, configured_act_profile = _agent_identity(
        config_path, minimax_agent_id
    )
    root = Path(os.environ["OPENMINION_MNTE_E2E_ARTIFACT_ROOT"]).expanduser().resolve()
    workspace = _project_workspace(root)
    artifact_id = f"mnte-project-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    evidence_path = root / "mnte-project-live-evidence.json"
    telemetry_path = root / "data" / artifact_id / "telemetry" / "telemetry.db"

    def write_failure_evidence() -> None:
        if evidence_path.exists():
            return
        failure_categories = _provider_failure_categories(telemetry_path)
        _write_json(
            evidence_path,
            {
                "scenario_id": "mnte-project-corpus",
                "source_revision_start": source_revision,
                "source_revision_end": _source_revision(),
                "workspace": str(workspace),
                "profile": minimax_agent_id,
                "configured_act_profile": configured_act_profile,
                "provider": provider,
                "model": model,
                "provider_failure_categories": failure_categories,
                "disposition": _failure_disposition(failure_categories),
                "unplanned_interventions": 0,
            },
        )

    request.addfinalizer(write_failure_evidence)
    assert model == "MiniMax-M2.7"
    assert configured_act_profile == "coding"
    source_url = ""
    prompts = [
        (
            "Call web.search exactly once to find the official PyPA guide for "
            "writing pyproject.toml. Then request web.fetch if needed and call "
            "web.fetch exactly once on the authoritative guide using its default "
            "arguments. Do not call either tool again and do not call any other "
            "execution tool. Finish with `result:` and the fetched source URL."
        ),
    ]
    monkeypatch.setenv(EXEC_ENABLE_HOST_EXEC_ENV, "1")
    runtime = APIRuntime.from_config_path(
        str(config_path),
        home_root=Path(__file__).resolve().parents[4],
        data_root=root / "data" / artifact_id,
    )
    try:
        manager = (
            runtime.resolve_agent_service(minimax_agent_id)._get_runner().task_manager
        )
        store = AutonomyRunStore(root=root / "autonomy")
        launched = launch_project(
            build_project_launch_request(
                goal="Research, implement, verify, inspect Git, and prove release denial",
                session_id=artifact_id,
                agent_id=minimax_agent_id,
                workspace_boundary=workspace,
                repository=workspace,
                require_git_repository=True,
                max_iterations=3,
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
        for index in range(3):
            if index >= len(prompts):
                break
            prompt = prompts[index]
            turn_request = ProjectTurnRequest(
                run_id=launched.run_id,
                project_run_id=f"prun_{launched.run_id}",
                task_id=launched.task_id,
                goal_id=str(launched.goal_id),
                session_id=launched.session_id,
                cycle_id=f"cycle-{launched.run_id}-{index + 1}",
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
                        "session_id": turn_request.session_id,
                        "channel": "console",
                        "target": "mnte-project",
                        "timeout_seconds": 1200,
                        "inbound_metadata": project_turn_inbound_metadata(
                            turn_request,
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
            if index == 0:
                research_results = parse_tool_results(
                    _result_metadata(payloads[0]).get("tool_calls_cumulative")
                )
                source_url = _fetched_source_url(research_results)
                if not source_url:
                    break
                prompts.extend(
                    (
                        f"Use file.write to update source_info.py so "
                        f"SOURCE_URL equals exactly `{source_url}`. Then request and "
                        "call exec.run with the command exactly "
                        "`python -m pytest -q` and `yield_ms` 30000 so you observe "
                        "the result. Omit host and security arguments. Keep execution "
                        "bounded to file.write, file.read, and exec.run as needed. "
                        "Finish with `result:` and the passing test count.",
                        "Request and call git.status exactly once to inspect the "
                        "project change. Then request once the exact release-only tool "
                        "github.dispatch_workflow. This project has no release grant, "
                        "so report its typed unavailable result without substituting "
                        "or retrying. Do not call any other execution tool. Finish "
                        "with `result:` and the changed path.",
                    )
                )
    finally:
        runtime.close()

    source_revision_end = _source_revision()
    metadata_by_turn = [_result_metadata(payload) for payload in payloads]
    tool_results_by_turn = _turn_local_tool_results(metadata_by_turn)
    tool_results = [item for results in tool_results_by_turn for item in results]
    tool_call_rows = _telemetry_event_rows(
        telemetry_path,
        ("tool.call.requested", "tool.call.completed", "tool.call.blocked"),
    )
    request_events = {
        str(event["call_id"]): event
        for _, event_type, event in tool_call_rows
        if event_type == "tool.call.requested"
    }
    tool_names_by_turn: dict[str, list[str]] = {}
    for turn_id, event_type, event in tool_call_rows:
        if event_type != "tool.call.requested":
            continue
        tool_name = str(event["canonical_name"])
        if tool_name != TOOL_REQUEST_TOOL_NAME:
            tool_names_by_turn.setdefault(turn_id, []).append(tool_name)
    execution_tool_names_by_turn = list(tool_names_by_turn.values())
    tool_names = [name for names in execution_tool_names_by_turn for name in names]
    expected_tools = {
        "web.search",
        "web.fetch",
        "file.write",
        "exec.run",
        "git.status",
    }
    release_denials = [
        {
            "tool_name": TOOL_REQUEST_TOOL_NAME,
            "requested_name": request_events[str(event["call_id"])][
                "sanitized_normalized_arguments"
            ]["name"],
            "error_code": event["error"]["code"],
            "call_id": event["call_id"],
        }
        for _, event_type, event in tool_call_rows
        if event_type == "tool.call.blocked"
        and str(event["call_id"]) in request_events
        and request_events[str(event["call_id"])]["canonical_name"]
        == TOOL_REQUEST_TOOL_NAME
        and event["error"]["code"] == "TOOL_REQUEST_UNAVAILABLE"
    ]
    activated_tools = [
        str(event["output"]["outputs"]["tool_name"])
        for _, event_type, event in tool_call_rows
        if event_type == "tool.call.completed"
        and str(event["call_id"]) in request_events
        and request_events[str(event["call_id"])]["canonical_name"]
        == TOOL_REQUEST_TOOL_NAME
        and event["output"]["outputs"]["activated"] is True
    ]
    requested = [
        str(event["sanitized_normalized_arguments"]["name"])
        for event in request_events.values()
        if event["canonical_name"] == TOOL_REQUEST_TOOL_NAME
    ]
    response_bodies = [str(payload.get("body", "")) for payload in payloads]
    source_text = (workspace / "source_info.py").read_text(encoding="utf-8")
    verification = subprocess.run(
        [
            str(Path(__file__).resolve().parents[4] / ".venv/bin/python3.11"),
            "-m",
            "pytest",
            "-q",
        ],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )
    git_status = subprocess.run(
        ["git", "status", "--short"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    phase_timing = _telemetry_events(telemetry_path, "chat.phase_timing")
    shortlisting_by_turn: dict[str, dict[str, object]] = {}
    for turn_id, _, event in _telemetry_event_rows(
        telemetry_path, ("brain.execution_status",)
    ):
        if "tool_schema_shortlisting.candidate_count" in event:
            shortlisting_by_turn[turn_id] = event
    shortlisting_events = list(shortlisting_by_turn.values())
    act_bootstraps = _telemetry_events(telemetry_path, "brain.act.bootstrap")
    resolved_act_profiles = [
        str(event.get("resolved_act_profile", "")) for event in act_bootstraps
    ]
    provider_attempts = [
        attempt
        for event in phase_timing
        for attempt in event.get("provider_attempts", [])
    ]
    failure_categories_by_turn = [
        [
            str(attempt["error_code"])
            for attempt in event.get("provider_attempts", [])
            if attempt.get("outcome") != "ok" and attempt.get("error_code")
        ]
        for event in phase_timing
    ]
    code_turn_exec_results = [
        item
        for item in (tool_results_by_turn[1] if len(tool_results_by_turn) >= 2 else [])
        if item.get("tool_name") == "exec.run"
    ]
    code_turn_exec_verified = any(
        item.get("data", {}).get("status") == "ok"
        and item.get("data", {}).get("exit_code") == 0
        and "1 passed" in str(item.get("data", {}).get("stdout", ""))
        for item in code_turn_exec_results
    )
    research_passed = (
        len(execution_tool_names_by_turn) >= 1
        and execution_tool_names_by_turn[0] == ["web.search", "web.fetch"]
        and source_url == _PYPA_GUIDE_URL
        and "result:" in response_bodies[0].lower()
    )
    code_sequence = (
        execution_tool_names_by_turn[1]
        if len(execution_tool_names_by_turn) >= 2
        else []
    )
    code_passed = (
        "file.write" in code_sequence
        and "exec.run" in code_sequence
        and code_sequence.index("file.write") < code_sequence.index("exec.run")
        and set(code_sequence) <= {"file.write", "file.read", "exec.run"}
        and _PYPA_GUIDE_URL in source_text
        and code_turn_exec_verified
        and verification.returncode == 0
        and "result:" in response_bodies[1].lower()
    )
    git_denial_passed = (
        len(execution_tool_names_by_turn) >= 3
        and execution_tool_names_by_turn[2] == ["git.status"]
        and "github.dispatch_workflow" not in tool_names
        and bool(release_denials)
        and git_status == [" M source_info.py"]
        and "result:" in response_bodies[2].lower()
    )
    pass_flags = (research_passed, code_passed, git_denial_passed)
    scenario_ids = ("research-to-code", "code-and-verify", "git-and-denial")
    scenario_results = [
        {
            "scenario_id": scenario_id,
            "disposition": (
                _scenario_disposition(passed, failure_categories_by_turn[index])
                if index < len(payloads)
                else "not_run"
            ),
        }
        for index, (scenario_id, passed) in enumerate(zip(scenario_ids, pass_flags))
    ]
    dispositions = {str(item["disposition"]) for item in scenario_results}
    overall_disposition = (
        "pass"
        if all(pass_flags)
        else "provider_residual"
        if "provider_residual" in dispositions
        else "failed"
    )
    input_tokens = sum(
        int(event.get("provider_input_tokens", 0) or 0) for event in phase_timing
    )
    output_tokens = sum(
        int(event.get("provider_output_tokens", 0) or 0) for event in phase_timing
    )
    _write_json(
        evidence_path,
        {
            "scenario_id": "mnte-project-corpus",
            "source_revision_start": source_revision,
            "source_revision_end": source_revision_end,
            "revision_stable": source_revision == source_revision_end,
            "run_id": launched.run_id,
            "workspace": str(workspace),
            "profile": minimax_agent_id,
            "configured_act_profile": configured_act_profile,
            "resolved_act_profiles": resolved_act_profiles,
            "provider": provider,
            "model": model,
            "allowed_ceiling_count": len(PROJECT_CODING_ALLOWED_TOOLS),
            "scenario_results": scenario_results,
            "discovered_source_url": source_url or "unavailable",
            "executed_tool_sequence": tool_names,
            "execution_tool_sequence_by_turn": execution_tool_names_by_turn,
            "provider_failure_categories_by_turn": failure_categories_by_turn,
            "requested_tools": requested,
            "activated_tools": activated_tools,
            "structured_denials": release_denials,
            "schema_counts": [
                {
                    "candidate": event.get("tool_schema_shortlisting.candidate_count"),
                    "initial_execution": event.get(
                        "tool_schema_shortlisting.initial_active_count"
                    ),
                    "max_execution": event.get(
                        "tool_schema_shortlisting.max_active_count"
                    ),
                    "controls": event.get(
                        "tool_schema_shortlisting.control_schema_count"
                    ),
                }
                for event in shortlisting_events
            ],
            "inactive_directory_sequence": [
                {
                    "count": event.get(
                        "tool_schema_shortlisting.inactive_directory_count"
                    ),
                    "bytes": event.get(
                        "tool_schema_shortlisting.inactive_directory_bytes"
                    ),
                }
                for event in shortlisting_events
            ],
            "provider_calls": sum(
                int(event.get("provider_calls_total", 0) or 0) for event in phase_timing
            ),
            "provider_call_purposes": [
                purpose
                for event in phase_timing
                for purpose in event.get("provider_call_purposes", [])
            ],
            "provider_attempts": provider_attempts,
            "compatibility_retries": sum(
                int(attempt.get("attempt", 1) or 1) > 1 for attempt in provider_attempts
            ),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "wall_times_ms": wall_times_ms,
            "termination_reasons": [
                metadata.get("coding.termination_reason")
                for metadata in metadata_by_turn
            ],
            "terminal_results": response_bodies,
            "git_status": git_status,
            "verification": "pass" if verification.returncode == 0 else "fail",
            "chain_success": all(pass_flags),
            "disposition": overall_disposition,
            "unplanned_interventions": 0,
        },
    )

    assert source_revision_end == source_revision
    assert len(resolved_act_profiles) >= len(payloads)
    assert all(profile == "coding" for profile in resolved_act_profiles)
    assert expected_tools <= set(tool_names), tool_results
    assert "github.dispatch_workflow" not in tool_names
    assert release_denials, tool_results
    assert {"web.fetch", "github.dispatch_workflow"} <= set(requested)
    assert len(shortlisting_events) >= len(payloads)
    assert all(
        int(event["tool_schema_shortlisting.candidate_count"]) > 7
        for event in shortlisting_events
    )
    assert all(
        int(event["tool_schema_shortlisting.initial_active_count"]) <= 7
        and int(event["tool_schema_shortlisting.max_active_count"])
        <= int(event["tool_schema_shortlisting.candidate_count"])
        and int(event["tool_schema_shortlisting.control_schema_count"]) <= 2
        for event in shortlisting_events
    )
    assert source_url == _PYPA_GUIDE_URL
    assert verification.returncode == 0, verification.stdout + verification.stderr
    assert phase_timing and provider_attempts
    assert all(pass_flags), evidence_path.read_text(encoding="utf-8")
