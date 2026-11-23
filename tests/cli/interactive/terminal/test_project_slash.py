from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from openminion.cli.interactive.terminal.shell.project import run_slash_project


def _render(command: str, runtime: object) -> str:
    output = io.StringIO()
    asyncio.run(
        run_slash_project(
            command,
            runtime=runtime,
            console=Console(file=output, force_terminal=False),
            approval_callback=None,
        )
    )
    return output.getvalue()


def test_project_slash_renders_help_without_runtime_dispatch() -> None:
    output = _render("/project help", SimpleNamespace())

    assert "/project answer RUN_ID" in output
    assert "/project redirect RUN_ID" in output
    assert "/project reprioritize RUN_ID" in output
    assert "/project extend-budget RUN_ID" in output
    assert "show/report RUN_ID" in output


def test_project_slash_dispatches_existing_control_commands() -> None:
    calls: list[str] = []
    runtime = SimpleNamespace(
        execute_project_control=lambda command: (
            calls.append(command) or "system",
            "project report",
        )
    )

    for command in (
        "/project answer run-1 --input-request-id input-1 --answer yes",
        "/project redirect run-1 --direction 'finish the report first'",
        "/project reprioritize run-1 --priority verify-first",
        "/project extend-budget run-1 --extra-iterations 1",
        "/project report run-1",
    ):
        assert "project report" in _render(command, runtime)

    assert calls == [
        "/project answer run-1 --input-request-id input-1 --answer yes",
        "/project redirect run-1 --direction 'finish the report first'",
        "/project reprioritize run-1 --priority verify-first",
        "/project extend-budget run-1 --extra-iterations 1",
        "/project report run-1",
    ]


def test_project_slash_renders_control_errors() -> None:
    def fail(_command: str) -> tuple[str, str]:
        raise ValueError("exact RUN_ID is required")

    output = _render(
        "/project report missing",
        SimpleNamespace(execute_project_control=fail),
    )

    assert "/project failed: exact RUN_ID is required" in output


@pytest.mark.parametrize("posture", ["readonly", "ask", "auto", "bypass"])
@pytest.mark.parametrize("approved", [False, True])
def test_project_start_always_requires_one_time_approval(
    posture: str, approved: bool
) -> None:
    output = io.StringIO()
    approval_calls: list[tuple[str, dict, object, dict]] = []
    launch_calls: list[str] = []
    deny_calls: list[str] = []
    request = SimpleNamespace(run=SimpleNamespace(run_id="run-1"))

    async def approve(
        tool_name: str,
        args: dict,
        call_id: object,
        policy_facts: dict,
    ) -> bool:
        approval_calls.append((tool_name, args, call_id, policy_facts))
        return approved

    runtime = SimpleNamespace(
        permission_mode=posture,
        prepare_project_command=lambda _command: request,
        project_launch_approval_args=lambda _request: {"goal": "ship safely"},
        launch_prepared_project=lambda _request: (
            launch_calls.append("run-1") or "system",
            "project launched",
        ),
        deny_prepared_project=lambda _request: (
            deny_calls.append("run-1") or "system",
            "project denied",
        ),
    )

    asyncio.run(
        run_slash_project(
            "/project start --goal 'ship safely'",
            runtime=runtime,
            console=Console(file=output, force_terminal=False),
            approval_callback=approve,
        )
    )

    assert approval_calls == [
        (
            "project.start",
            {"goal": "ship safely"},
            "run-1",
            {
                "canonical_tool": "project.start",
                "reason_code": "project_start_approval",
                "risk": {
                    "risk_class": "state_change",
                    "side_effects": "local",
                    "reversibility": "unknown",
                },
                "duration_options": ["allow_once", "deny"],
            },
        )
    ]
    assert launch_calls == (["run-1"] if approved else [])
    assert deny_calls == ([] if approved else ["run-1"])
    assert ("project launched" if approved else "project denied") in output.getvalue()


def test_project_start_rejects_inputs_changed_during_approval() -> None:
    output = io.StringIO()
    request = SimpleNamespace(run=SimpleNamespace(run_id="run-1"), path="app.py")
    launched = []

    async def approve(*args):
        request.path = "verify.py"
        return True

    runtime = SimpleNamespace(
        prepare_project_command=lambda command: request,
        project_launch_approval_args=lambda request: {"write_paths": [request.path]},
        launch_prepared_project=lambda request: launched.append(request),
    )
    asyncio.run(
        run_slash_project(
            "/project start --goal repair",
            runtime=runtime,
            console=Console(file=output, force_terminal=False),
            approval_callback=approve,
        )
    )
    assert not launched
    assert "launch inputs changed during approval" in output.getvalue()


@pytest.mark.parametrize("selector", ["agent_id", "config_ref"])
def test_project_start_rejects_selector_change_during_approval(tmp_path, selector):
    from openminion.cli.commands.project_launch import build_project_launch_request
    from openminion.cli.interactive.runtime.project import RuntimeProjectMixin

    request = build_project_launch_request(
        goal="repair",
        session_id="session",
        agent_id="repairer",
        workspace_boundary=tmp_path,
        repository=tmp_path,
        require_git_repository=False,
        config_ref="approved-config",
    )
    output = io.StringIO()
    launched = []

    async def approve(*args):
        setattr(request.run.execution_selectors, selector, "changed")
        return True

    runtime = SimpleNamespace(
        prepare_project_command=lambda command: request,
        project_launch_approval_args=RuntimeProjectMixin.project_launch_approval_args,
        launch_prepared_project=lambda request: launched.append(request),
    )
    asyncio.run(
        run_slash_project(
            "/project start --goal repair",
            runtime=runtime,
            console=Console(file=output, force_terminal=False),
            approval_callback=approve,
        )
    )
    assert not launched
    assert "launch inputs changed during approval" in output.getvalue()
