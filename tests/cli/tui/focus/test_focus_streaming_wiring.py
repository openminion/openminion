from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Input

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION
from openminion.cli.tui.focus.app import FocusApp
from openminion.cli.tui.focus.widgets import (
    FocusComposer,
    FocusMessageWidget,
    FocusStatusLine,
    FocusTranscript,
)
from openminion.cli.tui.presentation.models import ChatMessage, MessageKind


class _StreamingRuntimeDouble:
    contract_version = CLI_INTERFACE_VERSION

    def __init__(
        self,
        *,
        working_dir: str,
        chunks: list[str],
        raise_after: int | None = None,
        hold_after_first_chunk: bool = False,
    ) -> None:
        self._working_dir = str(Path(working_dir).resolve(strict=False))
        self._chunks = list(chunks)
        self._raise_after = raise_after
        self._hold_after_first_chunk = hold_after_first_chunk
        self._agent_id = "alpha"
        self._session_id = "focus-stream"
        self.tool_list: list[tuple[str, bool]] = []
        self.sent_texts: list[str] = []
        self.active_turns = 0
        self.max_active_turns = 0
        self.first_chunk_sent = asyncio.Event()
        self.release_first_turn = asyncio.Event()

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def transport(self) -> str:
        return "gateway"

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return "gpt-4.1-mini"

    @property
    def is_bound(self) -> bool:
        return True

    @property
    def working_dir(self) -> str:
        return self._working_dir

    def get_current_history(self) -> list[ChatMessage]:
        return []

    def list_sessions(self) -> list:
        return []

    def list_agents(self) -> list:
        return []

    def list_tools(self) -> list[tuple[str, bool]]:
        return list(self.tool_list)

    def switch_session(self, session_id: str) -> list[ChatMessage]:
        return []

    def switch_agent(self, agent_id: str) -> None:
        self._agent_id = str(agent_id or "").strip() or self._agent_id

    def new_session(self) -> str:
        return self._session_id

    def bind_session(self, session_id: str) -> None:
        self._session_id = session_id

    def create_new_session(self) -> str:
        return self._session_id

    def find_candidate_session(self):
        return None

    def list_directory_sessions(self, *, limit: int = 20):
        return []

    async def send_message(
        self,
        text: str,
        *,
        progress_callback=None,
        inbound_metadata=None,
        approval_callback=None,
    ):
        del progress_callback, inbound_metadata, approval_callback
        self.sent_texts.append(text)
        self.active_turns += 1
        self.max_active_turns = max(self.max_active_turns, self.active_turns)
        try:
            is_first_turn = len(self.sent_texts) == 1
            for i, chunk in enumerate(self._chunks):
                if self._raise_after is not None and i >= self._raise_after:
                    raise RuntimeError("simulated mid-stream failure")
                yield chunk
                if self._hold_after_first_chunk and is_first_turn and i == 0:
                    self.first_chunk_sent.set()
                    await self.release_first_turn.wait()
                await asyncio.sleep(0)
        finally:
            self.active_turns -= 1


def _make_app(runtime: _StreamingRuntimeDouble) -> FocusApp:
    return FocusApp(runtime=runtime, working_dir=runtime.working_dir)


@pytest.mark.asyncio
async def test_busy_focus_keeps_input_enabled_and_queues_next_message() -> None:
    runtime = _StreamingRuntimeDouble(
        working_dir="/tmp/focus-stream-queue",
        chunks=["reply"],
        hold_after_first_chunk=True,
    )
    app = _make_app(runtime)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("first"))
        await runtime.first_chunk_sent.wait()
        await pilot.pause()

        input_widget = app.screen.query_one("#focus-input", Input)
        assert input_widget.disabled is False

        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("second"))
        await pilot.pause()

        chat = app.screen.query_one(FocusTranscript)
        assert runtime.sent_texts == ["first"]
        assert any(
            msg.kind == MessageKind.USER and msg.body == "second"
            for msg in chat._messages
        )
        assert any(
            msg.kind == MessageKind.SYSTEM and "Queued message" in msg.body
            for msg in chat._messages
        )
        status_line = app.screen.query_one(FocusStatusLine)
        assert "queued: 1" in status_line._text()

        runtime.release_first_turn.set()
        for _ in range(30):
            await pilot.pause()
            if runtime.sent_texts == ["first", "second"] and not app.screen._busy:
                break

        assert runtime.sent_texts == ["first", "second"]
        assert app.screen._queued_count() == 0
        assert runtime.max_active_turns == 1


@pytest.mark.asyncio
async def test_escape_interrupt_preserves_queued_focus_message_without_running_it() -> None:
    runtime = _StreamingRuntimeDouble(
        working_dir="/tmp/focus-stream-queue-interrupt",
        chunks=["reply"],
        hold_after_first_chunk=True,
    )
    app = _make_app(runtime)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("first"))
        await runtime.first_chunk_sent.wait()
        await pilot.pause()

        app.screen.on_focus_composer_submitted(
            FocusComposer.Submitted("@README.md summarize")
        )
        await pilot.pause()

        assert app.screen._queued_count() == 1

        await app.screen._interrupt_current_turn()
        for _ in range(30):
            await pilot.pause()
            if not app.screen._busy:
                break

        assert runtime.sent_texts == ["first"]
        assert app.screen._queued_count() == 1
        assert runtime.max_active_turns == 1


@pytest.mark.asyncio
async def test_slash_command_while_busy_is_not_queued() -> None:
    runtime = _StreamingRuntimeDouble(
        working_dir="/tmp/focus-stream-busy-slash",
        chunks=["reply"],
        hold_after_first_chunk=True,
    )
    app = _make_app(runtime)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("first"))
        await runtime.first_chunk_sent.wait()
        await pilot.pause()

        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("/status"))
        await pilot.pause()

        chat = app.screen.query_one(FocusTranscript)
        assert app.screen._queued_count() == 0
        assert runtime.sent_texts == ["first"]
        assert any(
            msg.kind == MessageKind.SYSTEM and "transport  gateway" in msg.body
            for msg in chat._messages
        )

        runtime.release_first_turn.set()


@pytest.mark.asyncio
async def test_shell_escape_while_busy_does_not_replace_active_turn() -> None:
    runtime = _StreamingRuntimeDouble(
        working_dir="/tmp/focus-stream-busy-shell",
        chunks=["reply"],
        hold_after_first_chunk=True,
    )
    app = _make_app(runtime)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("first"))
        first_worker = app.screen._turn_worker
        await runtime.first_chunk_sent.wait()
        await pilot.pause()

        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("!pwd"))
        await pilot.pause()

        chat = app.screen.query_one(FocusTranscript)
        assert app.screen._turn_worker is first_worker
        assert app.screen._queued_count() == 0
        assert runtime.sent_texts == ["first"]
        assert any(
            msg.kind == MessageKind.SYSTEM and "Shell escape is unavailable" in msg.body
            for msg in chat._messages
        )

        runtime.release_first_turn.set()


@pytest.mark.asyncio
async def test_queued_focus_message_runs_after_first_turn_error() -> None:
    runtime = _StreamingRuntimeDouble(
        working_dir="/tmp/focus-stream-queue-error",
        chunks=["partial", "boom"],
        raise_after=1,
        hold_after_first_chunk=True,
    )
    app = _make_app(runtime)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("first"))
        await runtime.first_chunk_sent.wait()
        await pilot.pause()

        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("second"))
        await pilot.pause()
        assert app.screen._queued_count() == 1

        runtime.release_first_turn.set()
        for _ in range(40):
            await pilot.pause()
            if runtime.sent_texts == ["first", "second"] and not app.screen._busy:
                break

        chat = app.screen.query_one(FocusTranscript)
        assert runtime.sent_texts == ["first", "second"]
        assert app.screen._queued_count() == 0
        assert runtime.max_active_turns == 1
        assert any(
            msg.kind == MessageKind.ERROR and "simulated mid-stream failure" in msg.body
            for msg in chat._messages
        )


@pytest.mark.asyncio
async def test_streaming_chunks_arrive_in_order_into_transcript() -> None:
    runtime = _StreamingRuntimeDouble(
        working_dir="/tmp/focus-stream-order",
        chunks=["Hello", ", ", "stream", "ing ", "world!"],
    )
    app = _make_app(runtime)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("greet"))
        for _ in range(40):
            await pilot.pause()
            chat = app.screen.query_one(FocusTranscript)
            agents = [m for m in chat._messages if m.kind == MessageKind.AGENT]
            if agents and agents[-1].body == "Hello, streaming world!":
                break
        chat = app.screen.query_one(FocusTranscript)
        agents = [m for m in chat._messages if m.kind == MessageKind.AGENT]
        assert agents, "no AGENT message landed after streaming turn"
        assert agents[-1].body == "Hello, streaming world!"
        assert agents[-1].sender == "alpha"


@pytest.mark.asyncio
async def test_non_streaming_fallback_single_chunk() -> None:
    runtime = _StreamingRuntimeDouble(
        working_dir="/tmp/focus-stream-single",
        chunks=["one shot reply"],
    )
    app = _make_app(runtime)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("hi"))
        for _ in range(30):
            await pilot.pause()
            chat = app.screen.query_one(FocusTranscript)
            agents = [m for m in chat._messages if m.kind == MessageKind.AGENT]
            if agents and agents[-1].body == "one shot reply":
                break
        chat = app.screen.query_one(FocusTranscript)
        agents = [m for m in chat._messages if m.kind == MessageKind.AGENT]
        assert agents, "no AGENT message after single-chunk turn"
        assert agents[-1].body == "one shot reply"
        widgets = list(chat.query(FocusMessageWidget))
        assert widgets[-1]._streaming is None


@pytest.mark.asyncio
async def test_empty_chunks_drop_placeholder() -> None:
    runtime = _StreamingRuntimeDouble(
        working_dir="/tmp/focus-stream-empty",
        chunks=[],
    )
    app = _make_app(runtime)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("nothing"))
        for _ in range(15):
            await pilot.pause()
            chat = app.screen.query_one(FocusTranscript)
            if not app.screen._busy:
                break
        chat = app.screen.query_one(FocusTranscript)
        agents = [m for m in chat._messages if m.kind == MessageKind.AGENT]
        assert not agents, f"empty-reply turn left placeholder: {agents}"


@pytest.mark.asyncio
async def test_mid_stream_error_preserves_partial_reply() -> None:
    runtime = _StreamingRuntimeDouble(
        working_dir="/tmp/focus-stream-error",
        chunks=["partial-1 ", "partial-2 ", "after-error"],
        raise_after=2,  # raise on the 3rd chunk
    )
    app = _make_app(runtime)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("error-please"))
        for _ in range(30):
            await pilot.pause()
            chat = app.screen.query_one(FocusTranscript)
            errors = [m for m in chat._messages if m.kind == MessageKind.ERROR]
            if errors:
                break
        chat = app.screen.query_one(FocusTranscript)
        agents = [m for m in chat._messages if m.kind == MessageKind.AGENT]
        assert agents, "partial reply not preserved before mid-stream error"
        assert agents[-1].body == "partial-1 partial-2 "
        errors = [m for m in chat._messages if m.kind == MessageKind.ERROR]
        assert errors, "mid-stream error not surfaced inline"


@pytest.mark.asyncio
async def test_streaming_chunks_invoke_append_token() -> None:
    runtime = _StreamingRuntimeDouble(
        working_dir="/tmp/focus-stream-active",
        chunks=["a", "b", "c"],
    )
    app = _make_app(runtime)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        saw_streaming = False
        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("stream"))
        for _ in range(15):
            await pilot.pause()
            chat = app.screen.query_one(FocusTranscript)
            for w in chat.query(FocusMessageWidget):
                if w._streaming is not None:
                    saw_streaming = True
                    break
            if saw_streaming:
                break
        chat = app.screen.query_one(FocusTranscript)
        for _ in range(20):
            await pilot.pause()
            agents = [m for m in chat._messages if m.kind == MessageKind.AGENT]
            if agents and agents[-1].body == "abc":
                break
        agents = [m for m in chat._messages if m.kind == MessageKind.AGENT]
        assert agents and agents[-1].body == "abc"


@pytest.mark.asyncio
async def test_explicit_cancel_and_run_next_starts_reserved_queued_message() -> None:
    runtime = _StreamingRuntimeDouble(
        working_dir="/tmp/focus-stream-cancel-run-next",
        chunks=["reply"],
        hold_after_first_chunk=True,
    )
    app = _make_app(runtime)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("first"))
        await runtime.first_chunk_sent.wait()
        await pilot.pause()

        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("second"))
        await pilot.pause()
        assert app.screen._queued_count() == 1

        await app.screen._cancel_current_and_run_next()
        runtime.release_first_turn.set()
        for _ in range(40):
            await pilot.pause()
            if runtime.sent_texts == ["first", "second"] and not app.screen._busy:
                break

        assert runtime.sent_texts == ["first", "second"]
        assert app.screen._queued_count() == 0
        assert runtime.max_active_turns == 1
