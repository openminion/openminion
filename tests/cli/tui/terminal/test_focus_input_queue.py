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


class _MultiQueueRuntime:
    agent_id = "alpha"
    provider_name = "openai"
    model_name = "gpt-4.1-mini"
    permission_mode = "default"

    def __init__(self) -> None:
        self.sent_texts: list[str] = []
        self.first_chunk_sent = asyncio.Event()
        self.release_turns = asyncio.Event()
        self.third_turn_done = asyncio.Event()

    async def send_message(self, text: str, **kwargs):
        del kwargs
        self.sent_texts.append(text)
        if text == "first":
            yield "first chunk"
            self.first_chunk_sent.set()
            await self.release_turns.wait()
            yield " done"
        else:
            yield f"{text} reply"
            if text == "third":
                self.third_turn_done.set()
        await asyncio.sleep(0)


class _MultiQueueComposer:
    runtime: _MultiQueueRuntime
    last_instance: "_MultiQueueComposer | None" = None

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        self._calls = 0
        self.busy_events: list[bool] = []
        self.prompt_session = object()
        type(self).last_instance = self

    def set_busy(self, busy: bool) -> None:
        self.busy_events.append(bool(busy))

    async def read_line(self) -> str:
        self._calls += 1
        if self._calls == 1:
            return "first"
        if self._calls == 2:
            await type(self).runtime.first_chunk_sent.wait()
            return "second"
        if self._calls == 3:
            return "third"
        if self._calls == 4:
            await type(self).runtime.third_turn_done.wait()
            raise EOFError
        raise EOFError


class _BusyCommandRuntime:
    agent_id = "alpha"
    provider_name = "openai"
    model_name = "gpt-4.1-mini"
    permission_mode = "default"

    def __init__(self) -> None:
        self.sent_texts: list[str] = []
        self.first_chunk_sent = asyncio.Event()
        self.release_turn = asyncio.Event()

    async def send_message(self, text: str, **kwargs):
        del kwargs
        self.sent_texts.append(text)
        yield "reply"
        self.first_chunk_sent.set()
        await self.release_turn.wait()
        yield " done"
        await asyncio.sleep(0)


class _BusyCommandComposer:
    runtime: _BusyCommandRuntime

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        self._calls = 0
        self.prompt_session = object()

    def set_busy(self, busy: bool) -> None:
        del busy

    async def read_line(self) -> str:
        self._calls += 1
        if self._calls == 1:
            return "first"
        if self._calls == 2:
            await type(self).runtime.first_chunk_sent.wait()
            return "/status"
        if self._calls == 3:
            return "!pwd"
        if self._calls == 4:
            type(self).runtime.release_turn.set()
            raise EOFError
        raise EOFError


class _QueueCommandRuntime:
    agent_id = "alpha"
    provider_name = "openai"
    model_name = "gpt-4.1-mini"
    permission_mode = "default"

    def __init__(self) -> None:
        self.sent_texts: list[str] = []
        self.first_chunk_sent = asyncio.Event()
        self.release_turn = asyncio.Event()

    async def send_message(self, text: str, **kwargs):
        del kwargs
        self.sent_texts.append(text)
        if text == "first":
            yield "first chunk"
            self.first_chunk_sent.set()
            await self.release_turn.wait()
            yield " done"
        else:
            yield f"{text} reply"
        await asyncio.sleep(0)


class _QueueCommandComposer:
    runtime: _QueueCommandRuntime

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        self._calls = 0
        self.prompt_session = object()

    def set_busy(self, busy: bool) -> None:
        del busy

    async def read_line(self) -> str:
        self._calls += 1
        if self._calls == 1:
            return "first"
        if self._calls == 2:
            await type(self).runtime.first_chunk_sent.wait()
            return "second"
        if self._calls == 3:
            return "/queue"
        if self._calls == 4:
            return "/queue drop 1"
        if self._calls == 5:
            return "/queue"
        if self._calls == 6:
            return "third"
        if self._calls == 7:
            return "/queue clear"
        if self._calls == 8:
            type(self).runtime.release_turn.set()
            raise EOFError
        raise EOFError


class _LoopComposer:
    def __init__(self) -> None:
        self.busy_events: list[bool] = []
        self.prompt_session = object()

    def set_busy(self, busy: bool) -> None:
        self.busy_events.append(bool(busy))

    async def read_line(self) -> str:
        raise EOFError


class _SingleTurnRuntime:
    agent_id = "alpha"
    provider_name = "openai"
    model_name = "gpt-4.1-mini"
    permission_mode = "default"

    async def send_message(self, text: str, **kwargs):
        del text, kwargs
        yield "answer"


class _PromptGapComposer:
    prompt_session = object()

    async def read_line(self) -> str:
        raise EOFError


class _TTYInput:
    def isatty(self) -> bool:
        return True


class _ReplayRuntime:
    agent_id = "alpha"
    provider_name = "openai"
    model_name = "gpt-4.1-mini"
    permission_mode = "default"

    def __init__(self) -> None:
        self.sent_texts: list[str] = []
        self.turn_done = asyncio.Event()

    async def send_message(self, text: str, **kwargs):
        del kwargs
        self.sent_texts.append(text)
        await asyncio.sleep(0.02)
        yield "ok"
        self.turn_done.set()
        await asyncio.sleep(0)


class _ReplayComposer:
    runtime: _ReplayRuntime

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        self._calls = 0
        self._first_return_at = 0.0
        self.prompt_session = object()

    async def read_line(self) -> str:
        self._calls += 1
        if self._calls == 1:
            self._first_return_at = asyncio.get_running_loop().time()
            return "hi"
        if self._calls == 2:
            if (
                asyncio.get_running_loop().time() - self._first_return_at
                < terminal_shell._TYPEAHEAD_REOPEN_DELAY_SECONDS
            ):
                return "hi"
            await type(self).runtime.turn_done.wait()
            raise EOFError
        raise EOFError


def test_run_terminal_focus_swallows_top_level_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_interrupt(coroutine):
        coroutine.close()
        raise KeyboardInterrupt()

    monkeypatch.setattr(terminal_shell.asyncio, "run", _raise_interrupt)

    assert (
        terminal_shell.run_terminal_focus(object(), working_dir="/tmp/focus-exit") == 0
    )


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
        await asyncio.sleep(terminal_shell._TYPEAHEAD_REOPEN_DELAY_SECONDS * 2)
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
        msg.kind == MessageKind.SYSTEM and msg.body == "Running queued message: second"
        for msg in transcript._messages
    )
    assert any(
        msg.kind == MessageKind.USER and msg.body == "second"
        for msg in transcript._messages
    )


@pytest.mark.asyncio
async def test_terminal_focus_drains_multiple_queued_inputs_fifo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _MultiQueueRuntime()
    _MultiQueueComposer.runtime = runtime
    output = io.StringIO()

    monkeypatch.setattr(terminal_shell, "TerminalComposer", _MultiQueueComposer)
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
        await asyncio.sleep(terminal_shell._TYPEAHEAD_REOPEN_DELAY_SECONDS * 2)
        runtime.release_turns.set()

    asyncio.create_task(_release_first_turn())
    result = await terminal_shell._run_terminal_focus_async(
        runtime,
        working_dir="/tmp/focus-terminal-multi-queue",
        agent=None,
        session=None,
    )

    assert result == 0
    assert runtime.sent_texts == ["first", "second", "third"]

    transcript = _CapturedTranscript.last_instance
    assert transcript is not None
    queue_messages = [
        msg.body
        for msg in transcript._messages
        if msg.kind == MessageKind.SYSTEM and "Queued message" in msg.body
    ]
    assert queue_messages == [
        "Queued message (1 pending).",
        "Queued messages (2 pending).",
    ]
    running_messages = [
        msg.body
        for msg in transcript._messages
        if msg.kind == MessageKind.SYSTEM
        and msg.body.startswith("Running queued message:")
    ]
    assert running_messages == [
        "Running queued message: second",
        "Running queued message: third",
    ]
    composer = _MultiQueueComposer.last_instance
    assert composer is not None
    assert composer.busy_events[:3] == [True, True, True]
    assert composer.busy_events[-1] is False


@pytest.mark.asyncio
async def test_terminal_focus_busy_commands_are_not_queued_or_dispatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _BusyCommandRuntime()
    _BusyCommandComposer.runtime = runtime
    output = io.StringIO()

    monkeypatch.setattr(terminal_shell, "TerminalComposer", _BusyCommandComposer)
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

    result = await terminal_shell._run_terminal_focus_async(
        runtime,
        working_dir="/tmp/focus-terminal-busy-commands",
        agent=None,
        session=None,
    )

    assert result == 0
    assert runtime.sent_texts == ["first"]

    transcript = _CapturedTranscript.last_instance
    assert transcript is not None
    assert not any(
        msg.kind == MessageKind.USER and msg.body in {"/status", "!pwd"}
        for msg in transcript._messages
    )
    assert not any(
        msg.kind == MessageKind.SYSTEM and "Queued message" in msg.body
        for msg in transcript._messages
    )
    blocked_messages = [
        msg
        for msg in transcript._messages
        if msg.kind == MessageKind.SYSTEM
        and "Commands are unavailable while a turn is running" in msg.body
    ]
    assert len(blocked_messages) == 2


@pytest.mark.asyncio
async def test_terminal_focus_queue_commands_work_while_turn_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _QueueCommandRuntime()
    _QueueCommandComposer.runtime = runtime
    output = io.StringIO()

    monkeypatch.setattr(terminal_shell, "TerminalComposer", _QueueCommandComposer)
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

    result = await terminal_shell._run_terminal_focus_async(
        runtime,
        working_dir="/tmp/focus-terminal-queue-commands",
        agent=None,
        session=None,
    )

    assert result == 0
    assert runtime.sent_texts == ["first"]

    transcript = _CapturedTranscript.last_instance
    assert transcript is not None
    bodies = [msg.body for msg in transcript._messages]
    assert "Queued message (1 pending)." in bodies
    assert any("1. second" in body for body in bodies)
    assert any(body.startswith("Dropped queued message 1: second") for body in bodies)
    assert "No queued messages." in bodies
    assert "Cleared 1 queued message." in bodies
    assert not any(
        msg.kind == MessageKind.USER and msg.body in {"/queue", "/queue clear"}
        for msg in transcript._messages
    )


@pytest.mark.asyncio
async def test_terminal_focus_interrupt_preserves_queue_until_run_next() -> None:
    runtime = _QueueCommandRuntime()
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, width=120)
    transcript = terminal_shell.TerminalTranscript(console, plain_spinner=True)
    status_line = terminal_shell.TerminalStatusLine()
    composer = _LoopComposer()
    loop = terminal_shell._TerminalFocusLoop(
        runtime=runtime,
        console=console,
        transcript=transcript,
        status_line=status_line,
        composer=composer,
        overlay=object(),
        working_dir="/tmp/focus-terminal-interrupt",
        custom_commands={},
        approval_grants=set(),
    )

    await loop.start_turn("first")
    await runtime.first_chunk_sent.wait()
    await loop.handle_busy_input("second")
    loop.request_turn_interrupt()
    await loop.handle_turn_completion()

    assert runtime.sent_texts == ["first"]
    assert list(loop.pending_turns) == ["second"]
    assert loop.queue_auto_drain_paused is True
    assert loop.active_turn_task is None
    assert any(
        msg.kind == MessageKind.SYSTEM and "Preserved 1 queued message" in msg.body
        for msg in transcript._messages
    )

    await loop.handle_queue_command("/queue run-next")
    await loop.handle_turn_completion()
    await loop.cancel_read_task()

    assert runtime.sent_texts == ["first", "second"]
    assert list(loop.pending_turns) == []
    assert loop.queue_auto_drain_paused is False
    assert any(
        msg.kind == MessageKind.SYSTEM and msg.body == "Running queued message: second"
        for msg in transcript._messages
    )


@pytest.mark.asyncio
async def test_terminal_focus_adds_one_gap_before_typeahead_prompt() -> None:
    runtime = _QueueCommandRuntime()
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, width=120)
    transcript = terminal_shell.TerminalTranscript(console, plain_spinner=True)
    loop = terminal_shell._TerminalFocusLoop(
        runtime=runtime,
        console=console,
        transcript=transcript,
        status_line=terminal_shell.TerminalStatusLine(),
        composer=_PromptGapComposer(),
        overlay=object(),
        working_dir="/tmp/focus-terminal-prompt-gap",
        custom_commands={},
        approval_grants=set(),
    )

    loop.start_read_task(leading_blank_lines=1)

    assert loop.read_task is not None
    with pytest.raises(EOFError):
        await loop.read_task

    assert output.getvalue() == "\n"


@pytest.mark.asyncio
async def test_terminal_focus_adds_one_gap_after_idle_answer() -> None:
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, width=120)
    transcript = terminal_shell.TerminalTranscript(console, plain_spinner=True)
    transcript.set_terminal_writer(lambda render: render())
    loop = terminal_shell._TerminalFocusLoop(
        runtime=_SingleTurnRuntime(),
        console=console,
        transcript=transcript,
        status_line=terminal_shell.TerminalStatusLine(),
        composer=_LoopComposer(),
        overlay=object(),
        working_dir="/tmp/focus-terminal-question-gap",
        custom_commands={},
        approval_grants=set(),
    )

    await loop.handle_idle_input("question?")
    await loop.handle_turn_completion()
    await loop.cancel_read_task()

    rendered = output.getvalue()
    assert rendered.startswith("⏺ answer")
    assert rendered.endswith("\n\n")


@pytest.mark.asyncio
async def test_terminal_focus_ignores_immediate_prompt_replay_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ReplayRuntime()
    _ReplayComposer.runtime = runtime
    output = io.StringIO()

    monkeypatch.setattr(terminal_shell, "TerminalComposer", _ReplayComposer)
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

    result = await terminal_shell._run_terminal_focus_async(
        runtime,
        working_dir="/tmp/focus-terminal-replay",
        agent=None,
        session=None,
    )

    assert result == 0
    assert runtime.sent_texts == ["hi"]

    transcript = _CapturedTranscript.last_instance
    assert transcript is not None
    assert not any(
        msg.kind == MessageKind.SYSTEM and "Queued message" in msg.body
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
