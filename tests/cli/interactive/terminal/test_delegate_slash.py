from __future__ import annotations

import asyncio
import json
import threading

import pytest
from rich.console import Console

from openminion.cli.interactive.terminal.shell.actions import _handle_slash
from openminion.cli.interactive.terminal.shell.delegation import (
    delegation_start_message,
    handle_slash_delegate,
)
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine


def test_terminal_slash_delegate_forwards_approval_callback() -> None:
    calls: list[dict[str, object]] = []
    approval_callback = object()

    class _Runtime:
        def delegate_task(self, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            return {
                "ok": True,
                "mode": kwargs.get("mode"),
                "status": "success",
                "agent_id": kwargs.get("target_agent_id"),
                "content": "delegated",
            }

    console = Console(record=True, force_terminal=False)
    handle_slash_delegate(
        "/delegate worker what's weather at sf?",
        runtime=_Runtime(),
        console=console,
        approval_callback=approval_callback,  # type: ignore[arg-type]
    )

    assert len(calls) == 1
    assert calls[0]["approval_callback"] is approval_callback
    assert calls[0]["target_agent_id"] == "worker"
    assert calls[0]["instruction"] == "what's weather at sf?"
    assert "Delegation:" in console.export_text()


def test_terminal_slash_delegate_forwards_review_request() -> None:
    calls: list[dict[str, object]] = []

    class _Runtime:
        def delegate_task(self, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            return {
                "ok": True,
                "mode": "review",
                "status": "passed",
                "agent_id": kwargs.get("target_agent_id"),
            }

    request = {
        "reviewer_agent_id": "reviewer",
        "instruction": "Review the child patch.",
        "review_criteria": ["No blocking findings."],
        "repository_instructions": "Follow AGENTS.md.",
        "child_artifact": {"record_alias": "artifact-1"},
    }
    console = Console(record=True, force_terminal=False)
    handle_slash_delegate(
        "/delegate review " + json.dumps(request, separators=(",", ":")),
        runtime=_Runtime(),
        console=console,
    )

    assert calls[0]["target_agent_id"] == "reviewer"
    assert calls[0]["instruction"] == "Review the child patch."
    assert calls[0]["review_criteria"] == ("No blocking findings.",)
    assert calls[0]["repository_instructions"] == "Follow AGENTS.md."
    assert calls[0]["child_artifact"] == {"record_alias": "artifact-1"}
    assert "status    passed" in console.export_text()


@pytest.mark.asyncio
async def test_terminal_slash_delegate_keeps_async_approval_responsive() -> None:
    loop = asyncio.get_running_loop()
    callback_loop: asyncio.AbstractEventLoop | None = None

    async def approval_callback(*_args: object) -> bool:
        nonlocal callback_loop
        callback_loop = asyncio.get_running_loop()
        return True

    class _Runtime:
        def delegate_task(self, **kwargs: object) -> dict[str, object]:
            callback = kwargs["approval_callback"]
            approved = callback("file.write", {"path": "marker.txt"}, "call-1")
            return {
                "ok": approved,
                "mode": kwargs.get("mode"),
                "status": "success",
                "agent_id": kwargs.get("target_agent_id"),
                "content": "delegated",
            }

    console = Console(record=True, force_terminal=False)
    exited = await _handle_slash(
        "/delegate worker write file",
        runtime=_Runtime(),
        console=console,
        transcript=TerminalTranscript(console),
        overlay=object(),
        status_line=TerminalStatusLine(),
        working_dir=".",
        approval_callback=approval_callback,
    )

    assert exited is False
    assert callback_loop is loop
    assert "Delegation:" in console.export_text()


@pytest.mark.asyncio
async def test_terminal_slash_delegate_announces_work_before_completion() -> None:
    started = threading.Event()
    release = threading.Event()

    class _Runtime:
        def delegate_task(self, **kwargs: object) -> dict[str, object]:
            started.set()
            assert release.wait(timeout=1.0)
            return {
                "ok": True,
                "mode": kwargs.get("mode"),
                "status": "success",
                "agent_id": kwargs.get("target_agent_id"),
                "content": "delegated",
            }

    console = Console(record=True, force_terminal=False)
    task = asyncio.create_task(
        _handle_slash(
            "/delegate worker inspect weather",
            runtime=_Runtime(),
            console=console,
            transcript=TerminalTranscript(console),
            overlay=object(),
            status_line=TerminalStatusLine(),
            working_dir=".",
        )
    )

    assert await asyncio.to_thread(started.wait, 1.0)
    assert "Delegating to worker..." in console.export_text()
    release.set()
    await task

    assert "Delegation:" in console.export_text()


def test_delegation_start_message_ignores_non_start_modes() -> None:
    assert delegation_start_message("/delegate status task-1") == ""
    assert delegation_start_message("/delegate") == ""
