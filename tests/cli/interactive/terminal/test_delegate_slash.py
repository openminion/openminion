from __future__ import annotations

import asyncio

import pytest
from rich.console import Console

from openminion.cli.interactive.terminal.shell.actions import _handle_slash
from openminion.cli.interactive.terminal.shell.delegation import handle_slash_delegate


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
        "/delegate worker write file",
        runtime=_Runtime(),
        console=console,
        approval_callback=approval_callback,  # type: ignore[arg-type]
    )

    assert len(calls) == 1
    assert calls[0]["approval_callback"] is approval_callback
    assert calls[0]["target_agent_id"] == "worker"
    assert calls[0]["instruction"] == "write file"
    assert "Delegation:" in console.export_text()


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
        transcript=object(),
        overlay=object(),
        status_line=object(),
        working_dir=".",
        approval_callback=approval_callback,
    )

    assert exited is False
    assert callback_loop is loop
    assert "Delegation:" in console.export_text()
