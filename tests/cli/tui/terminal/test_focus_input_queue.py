from __future__ import annotations

import asyncio
import io

import pytest
from rich.console import Console

from openminion.cli.tui.presentation.models import MessageKind
from openminion.cli.tui.terminal import shell as terminal_shell
from openminion.cli.tui.terminal.transcript import TerminalTranscript as _BaseTranscript


class _QueueRuntime:
    agent_id = "alpha"
    provider_name = "openai"
    model_name = "gpt-4.1-mini"
    permission_mode = "default"

    def __init__(self) -> None:
        self.sent_texts: list[str] = []
        self.first_chunk_sent = asyncio.Event()
        self.release_first_turn = asyncio.Event()
        self.second_turn_done = asyncio.Event()

    async def send_message(self, text: str, **kwargs):
        del kwargs
        self.sent_texts.append(text)
        if text == "first":
            yield "first chunk"
            self.first_chunk_sent.set()
            await self.release_first_turn.wait()
            yield " done"
        else:
            yield "second reply"
            self.second_turn_done.set()
        await asyncio.sleep(0)


class _CapturedTranscript(_BaseTranscript):
    last_instance: "_CapturedTranscript | None" = None

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        type(self).last_instance = self


class _ScriptedComposer:
    runtime: _QueueRuntime

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        self._calls = 0
        self.prompt_session = object()

    async def read_line(self) -> str:
        self._calls += 1
        if self._calls == 1:
            return "first"
        if self._calls == 2:
            await type(self).runtime.first_chunk_sent.wait()
            return "second"
        if self._calls == 3:
            await type(self).runtime.second_turn_done.wait()
            raise EOFError
        raise EOFError


class _TTYInput:
    def isatty(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_terminal_focus_keeps_accepting_input_while_turn_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _QueueRuntime()
    _ScriptedComposer.runtime = runtime
    output = io.StringIO()

    monkeypatch.setattr(terminal_shell, "TerminalComposer", _ScriptedComposer)
    monkeypatch.setattr(terminal_shell, "TerminalTranscript", _CapturedTranscript)
    monkeypatch.setattr(
        terminal_shell,
        "Console",
        lambda: Console(file=output, force_terminal=False, width=120),
    )
    monkeypatch.setattr(terminal_shell, "_push_greeter", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        terminal_shell, "_schedule_startup_notice", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(terminal_shell.sys, "stdin", _TTYInput())
    monkeypatch.setattr(terminal_shell, "statusline_label", lambda runtime: "")

    async def _release_first_turn() -> None:
        await runtime.first_chunk_sent.wait()
        await asyncio.sleep(0.01)
        runtime.release_first_turn.set()

    asyncio.create_task(_release_first_turn())
    result = await terminal_shell._run_terminal_focus_async(
        runtime,
        working_dir="/tmp/focus-terminal-queue",
        agent=None,
        session=None,
    )

    assert result == 0
    assert runtime.sent_texts == ["first", "second"]

    transcript = _CapturedTranscript.last_instance
    assert transcript is not None
    assert any(
        msg.kind == MessageKind.SYSTEM and "Queued message (1 pending)." in msg.body
        for msg in transcript._messages
    )
    assert any(
        msg.kind == MessageKind.USER and msg.body == "second"
        for msg in transcript._messages
    )


@pytest.mark.asyncio
async def test_terminal_approval_callback_pauses_prompt_and_resumes_afterward() -> None:
    events: list[str] = []

    class _Overlay:
        async def present_approval_async(self, prompt: str) -> str:
            events.append(f"prompt:{prompt}")
            return "allow"

    async def _pause_prompt() -> None:
        events.append("pause")

    def _resume_prompt() -> None:
        events.append("resume")

    callback = terminal_shell._build_terminal_approval_callback(
        overlay=_Overlay(),
        session_grants=set(),
        pause_prompt=_pause_prompt,
        resume_prompt=_resume_prompt,
    )

    approved = await callback("file.write", {"path": "scratch.txt"}, "call-1")

    assert approved is True
    assert events[0] == "pause"
    assert events[-1] == "resume"
    assert len(events) == 3
    assert events[1].startswith("prompt:Approval required: file.write(")
    assert "scratch.txt" in events[1]
