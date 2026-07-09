from __future__ import annotations

import asyncio
from pathlib import Path

from openminion.cli.tui.terminal.shell import run_terminal_focus


class FakeFocusRuntime:
    agent_id = "fake-focus"
    session_id = "fake-session"
    provider_name = "fake"
    model_name = "deterministic"
    permission_mode = "default"
    transport = "fake"

    def token_usage_snapshot(self):
        return None

    async def send_message(self, text: str, **kwargs):
        progress_callback = kwargs.get("progress_callback")
        prompt = str(text or "").strip()
        if prompt == "slow queue":
            await self._emit_status(progress_callback, "Preparing turn...")
            await asyncio.sleep(0.45)
            await self._emit_status(progress_callback, "Loading memory context...")
            await asyncio.sleep(0.75)
            await self._emit_status(progress_callback, "Analyzing request...")
            await asyncio.sleep(0.15)
            yield "first reply complete"
            return
        if prompt == "queued follow-up":
            await asyncio.sleep(0.05)
            yield "queued reply complete"
            return
        if prompt == "slow interrupt":
            await self._emit_status(progress_callback, "Preparing interrupt proof...")
            try:
                while True:
                    await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                raise
        if prompt == "repeat tools":
            await self._emit_repeated_tools(progress_callback)
            yield "repeat tool proof complete"
            return
        yield f"echo: {prompt}"

    async def _emit_status(self, progress_callback, label: str) -> None:
        if callable(progress_callback):
            progress_callback(
                {
                    "trace_id": "fake-focus-progress",
                    "status_key": "working",
                    "label": label,
                }
            )
        await asyncio.sleep(0)

    async def _emit_repeated_tools(self, progress_callback) -> None:
        if not callable(progress_callback):
            return
        base = {
            "tool_name": "web.search",
            "args": {"query": "MSFT stock"},
        }
        for index in range(2):
            call_id = f"repeat-success-{index}"
            progress_callback({"kind": "tool_started", "call_id": call_id, **base})
            await asyncio.sleep(0)
            progress_callback(
                {
                    "kind": "tool_completed",
                    "call_id": call_id,
                    **base,
                    "content": "stock result",
                    "exit_code": 0,
                }
            )
            await asyncio.sleep(0)
        for index in range(2):
            call_id = f"repeat-failure-{index}"
            progress_callback({"kind": "tool_started", "call_id": call_id, **base})
            await asyncio.sleep(0)
            progress_callback(
                {
                    "kind": "tool_completed",
                    "call_id": call_id,
                    **base,
                    "content": "tool_budget_calls_exceeded",
                    "exit_code": 1,
                }
            )
            await asyncio.sleep(0)


if __name__ == "__main__":
    raise SystemExit(
        run_terminal_focus(
            FakeFocusRuntime(),
            working_dir=str(Path.cwd()),
            agent="fake-focus",
            session="fake-session",
            plain_spinner=True,
            startup_notice=None,
        )
    )
