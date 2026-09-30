from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

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
        "/project reprioritize run-1 --priority verify-first",
        "/project extend-budget run-1 --extra-iterations 1",
        "/project report run-1",
    ):
        assert "project report" in _render(command, runtime)

    assert calls == [
        "/project answer run-1 --input-request-id input-1 --answer yes",
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
