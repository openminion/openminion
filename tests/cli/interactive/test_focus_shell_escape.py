from __future__ import annotations

import tempfile

import pytest

from openminion.cli.interactive.app import FocusApp, _DemoFocusRuntime
from openminion.cli.interactive.screen import FocusScreen
from openminion.cli.interactive.widgets import FocusComposer, FocusTranscript
from openminion.cli.presentation.models import MessageKind


def _make_app(tmp: str) -> FocusApp:
    runtime = _DemoFocusRuntime(working_dir=tmp)
    return FocusApp(runtime=runtime, working_dir=tmp)


@pytest.mark.asyncio
async def test_shell_escape_renders_stdout_as_tool_block() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, FocusScreen)
            app.screen.on_focus_composer_submitted(
                FocusComposer.Submitted("!echo hello")
            )
            for _ in range(40):
                await pilot.pause()
                chat = app.screen.query_one(FocusTranscript)
                tools = [m for m in chat._messages if m.kind == MessageKind.TOOL]
                if tools:
                    break
            chat = app.screen.query_one(FocusTranscript)
            tools = [m for m in chat._messages if m.kind == MessageKind.TOOL]
            assert tools, "no TOOL message rendered after shell escape"
            assert "hello" in (tools[-1].tool_result or "")
            assert tools[-1].tool_event is not None
            assert tools[-1].tool_event.exit_code == 0
            assert tools[-1].tool_event.tool_name == "bash"


@pytest.mark.asyncio
async def test_shell_escape_includes_user_command_in_transcript() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.on_focus_composer_submitted(FocusComposer.Submitted("!echo hi"))
            for _ in range(40):
                await pilot.pause()
                chat = app.screen.query_one(FocusTranscript)
                tools = [m for m in chat._messages if m.kind == MessageKind.TOOL]
                if tools:
                    break
            chat = app.screen.query_one(FocusTranscript)
            users = [m for m in chat._messages if m.kind == MessageKind.USER]
            assert users, "user command not echoed"
            assert users[-1].body == "!echo hi"


@pytest.mark.asyncio
async def test_shell_escape_with_empty_command_is_no_op() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            chat = app.screen.query_one(FocusTranscript)
            tool_ids_before = {
                m.msg_id for m in chat._messages if m.kind == MessageKind.TOOL
            }
            app.screen.on_focus_composer_submitted(FocusComposer.Submitted("!   "))
            await pilot.pause()
            await pilot.pause()
            chat = app.screen.query_one(FocusTranscript)
            tool_ids_after = {
                m.msg_id for m in chat._messages if m.kind == MessageKind.TOOL
            }
            new_tools = tool_ids_after - tool_ids_before
            assert not new_tools, (
                f"empty `!` should be a no-op; new TOOL ids: {new_tools}"
            )


@pytest.mark.asyncio
async def test_shell_escape_unknown_command_surfaces_inline_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.on_focus_composer_submitted(
                FocusComposer.Submitted("!nonexistent-fns09-xyz-binary")
            )
            for _ in range(20):
                await pilot.pause()
                chat = app.screen.query_one(FocusTranscript)
                errors = [m for m in chat._messages if m.kind == MessageKind.ERROR]
                if errors:
                    break
            chat = app.screen.query_one(FocusTranscript)
            errors = [m for m in chat._messages if m.kind == MessageKind.ERROR]
            assert errors, "unknown command did not surface inline error"


@pytest.mark.asyncio
async def test_shell_escape_unbalanced_quotes_surfaces_parse_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.on_focus_composer_submitted(
                FocusComposer.Submitted('!echo "unbalanced')
            )
            for _ in range(15):
                await pilot.pause()
                chat = app.screen.query_one(FocusTranscript)
                errors = [m for m in chat._messages if m.kind == MessageKind.ERROR]
                if errors:
                    break
            chat = app.screen.query_one(FocusTranscript)
            errors = [m for m in chat._messages if m.kind == MessageKind.ERROR]
            assert errors, "shlex parse error not surfaced"
            assert "Could not parse" in errors[-1].body


@pytest.mark.asyncio
async def test_shell_escape_captures_stderr_into_tool_block() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            # `sh -c "echo out; echo err >&2"` writes to both streams.
            app.screen.on_focus_composer_submitted(
                FocusComposer.Submitted('!sh -c "echo out; echo err >&2"')
            )
            for _ in range(40):
                await pilot.pause()
                chat = app.screen.query_one(FocusTranscript)
                tools = [m for m in chat._messages if m.kind == MessageKind.TOOL]
                if tools:
                    break
            chat = app.screen.query_one(FocusTranscript)
            tools = [m for m in chat._messages if m.kind == MessageKind.TOOL]
            assert tools, "no TOOL block for stderr test"
            body = tools[-1].tool_result or ""
            assert "out" in body
            assert "err" in body
