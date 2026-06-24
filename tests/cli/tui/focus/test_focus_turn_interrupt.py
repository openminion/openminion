from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Input, Label

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION
from openminion.cli.tui.focus.app import FocusApp
from openminion.cli.tui.focus.screen import FocusScreen
from openminion.cli.tui.focus.widgets import FocusStatusLine, ToolApprovalWidget
from openminion.cli.tui.focus.widgets import FocusComposer, FocusTranscript
from openminion.cli.tui.presentation.models import ChatMessage, MessageKind


class _InterruptRuntimeDouble:
    contract_version = CLI_INTERFACE_VERSION

    def __init__(
        self,
        *,
        working_dir: str,
        emit_partial_first: bool = False,
        require_approval: bool = False,
        final_text: str = "done",
    ) -> None:
        self._working_dir = str(Path(working_dir).resolve(strict=False))
        self._emit_partial_first = bool(emit_partial_first)
        self._require_approval = bool(require_approval)
        self._final_text = str(final_text)
        self._agent_id = "alpha"
        self._session_id = "focus-interrupt"
        self.started = asyncio.Event()
        self.first_chunk_sent = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.tool_list = [("exec.run", True), ("file.read", True)]

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

    def list_sessions(self) -> list[Any]:
        return []

    def list_agents(self) -> list[Any]:
        return []

    def list_tools(self) -> list[tuple[str, bool]]:
        return list(self.tool_list)

    def switch_session(self, session_id: str) -> list[ChatMessage]:
        self._session_id = str(session_id)
        return []

    def switch_agent(self, agent_id: str) -> None:
        self._agent_id = str(agent_id or "").strip() or self._agent_id

    def new_session(self) -> str:
        return self.create_new_session()

    def bind_session(self, session_id: str) -> None:
        self._session_id = str(session_id or "").strip() or self._session_id

    def create_new_session(self) -> str:
        self._session_id = "focus-interrupt-new"
        return self._session_id

    def find_candidate_session(self):
        return None

    def list_directory_sessions(self, *, limit: int = 20):
        del limit
        return []

    async def send_message(
        self,
        text: str,
        *,
        progress_callback=None,
        inbound_metadata=None,
        approval_callback=None,
    ):
        del progress_callback, inbound_metadata, text
        self.started.set()
        try:
            if self._require_approval and approval_callback is not None:
                await approval_callback("exec.run", {"command": "pwd"}, "call-1")
            if self._emit_partial_first:
                yield "partial reply"
                self.first_chunk_sent.set()
            await self.release.wait()
            yield self._final_text
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _make_app(runtime: _InterruptRuntimeDouble) -> FocusApp:
    return FocusApp(runtime=runtime, working_dir=runtime.working_dir)


def _system_bodies(chat: FocusTranscript) -> list[str]:
    return [str(msg.body) for msg in chat._messages if msg.kind == MessageKind.SYSTEM]


@pytest.mark.asyncio
async def test_idle_ctrl_c_does_not_open_interrupt_prompt() -> None:
    runtime = _InterruptRuntimeDouble(working_dir="/tmp/focus-interrupt-idle")
    app = _make_app(runtime)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        await pilot.press("ctrl+c")
        await pilot.pause()

        assert isinstance(app.screen, FocusScreen)
        assert not list(app.screen.query(".focus-inline-prompt")), (
            "idle ctrl+c must not open the interrupt prompt"
        )


@pytest.mark.asyncio
async def test_busy_escape_prompts_interrupt_and_decline_keeps_turn_running() -> None:
    runtime = _InterruptRuntimeDouble(
        working_dir="/tmp/focus-interrupt-decline",
        final_text="finished",
    )
    app = _make_app(runtime)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("hello"))
        await runtime.started.wait()
        await pilot.pause()
        assert app.screen._busy is True

        app.screen.action_handle_escape()
        await pilot.pause()
        prompt = app.screen.query_one(".focus-inline-prompt-title", Label)
        assert "Interrupt current turn?" in str(prompt.render())

        await pilot.press("n")
        await pilot.pause()
        assert app.screen._busy is True, "declining interrupt must keep the turn alive"

        runtime.release.set()
        await pilot.pause()
        await pilot.pause()

        chat = app.screen.query_one(FocusTranscript)
        assert "Interrupted current turn." not in _system_bodies(chat)
        assert any(
            msg.kind == MessageKind.AGENT and msg.body == "finished"
            for msg in chat._messages
        )


@pytest.mark.asyncio
async def test_ctrl_c_confirms_interrupt_preserves_partial_reply_and_recovers() -> None:
    runtime = _InterruptRuntimeDouble(
        working_dir="/tmp/focus-interrupt-confirm",
        emit_partial_first=True,
        final_text="should-not-complete",
    )
    app = _make_app(runtime)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("hello"))
        await runtime.started.wait()
        await runtime.first_chunk_sent.wait()
        await pilot.pause()
        assert app.screen._busy is True

        await pilot.press("ctrl+c")
        await pilot.pause()
        prompt = app.screen.query_one(".focus-inline-prompt-title", Label)
        assert "Interrupt current turn?" in str(prompt.render())
        await pilot.press("y")
        await pilot.pause()
        await pilot.pause()

        chat = app.screen.query_one(FocusTranscript)
        status_line = app.screen.query_one(FocusStatusLine)
        input_widget = app.screen.query_one("#focus-input", Input)

        assert runtime.cancelled is True
        assert app.screen._busy is False
        assert input_widget.disabled is False
        assert "palette" in status_line._text().lower(), status_line._text()
        assert any(
            msg.kind == MessageKind.AGENT and msg.body == "partial reply"
            for msg in chat._messages
        ), chat._messages
        assert "Interrupted current turn." in _system_bodies(chat)
        assert not list(app.screen.query(".focus-inline-prompt"))


@pytest.mark.asyncio
async def test_interrupt_dismisses_tool_approval_widget_cleanly() -> None:
    runtime = _InterruptRuntimeDouble(
        working_dir="/tmp/focus-interrupt-approval",
        require_approval=True,
    )
    app = _make_app(runtime)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        app.screen.on_focus_composer_submitted(FocusComposer.Submitted("run pwd"))
        await runtime.started.wait()
        await pilot.pause()
        await pilot.pause()

        assert list(app.screen.query(ToolApprovalWidget)), (
            "approval widget should be visible while the turn awaits approval"
        )

        app.screen.action_handle_escape()
        await pilot.pause()
        prompt = app.screen.query_one(".focus-inline-prompt-title", Label)
        assert "Interrupt current turn?" in str(prompt.render())
        await pilot.press("y")
        await pilot.pause()
        await pilot.pause()

        assert runtime.cancelled is True
        assert app.screen._busy is False
        assert not list(app.screen.query(ToolApprovalWidget)), (
            "interrupt must dismiss any approval widget owned by the turn"
        )
        assert "Interrupted current turn." in _system_bodies(
            app.screen.query_one(FocusTranscript)
        )
