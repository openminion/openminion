from __future__ import annotations

import tempfile

import pytest
from textual.widgets import Input

from openminion.cli.interactive.app import FocusApp, _DemoFocusRuntime
from openminion.cli.interactive.widgets.slash_overlay import SlashCommandOverlay


def _make_app(tmp: str) -> FocusApp:
    runtime = _DemoFocusRuntime(working_dir=tmp)
    return FocusApp(runtime=runtime, working_dir=tmp)


@pytest.mark.asyncio
async def test_overlay_hidden_until_input_starts_with_slash() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.screen.query_one(SlashCommandOverlay)
            assert overlay.visible is False, (
                "overlay should start hidden — visible only after `/`"
            )
            inp = app.screen.query_one("#focus-input", Input)
            inp.value = "hello world"
            await pilot.pause()
            assert overlay.visible is False


@pytest.mark.asyncio
async def test_overlay_opens_when_input_starts_with_slash() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.screen.query_one(SlashCommandOverlay)
            inp = app.screen.query_one("#focus-input", Input)
            inp.value = "/"
            await pilot.pause()
            assert overlay.visible is True, "overlay must show when input is `/`"
            assert overlay.filtered, "overlay must seed the registry on `/`"
            screen = app.screen
            registered = [
                aliases[0] for aliases, _d, _h in screen._slash_command_registry
            ]
            filtered_names = [name for name, _ in overlay.filtered]
            for cmd in registered:
                assert cmd in filtered_names, (
                    f"command {cmd!r} missing from autocomplete on bare /"
                )


@pytest.mark.asyncio
async def test_typing_narrows_overlay_list_via_prefix_match() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.screen.query_one(SlashCommandOverlay)
            inp = app.screen.query_one("#focus-input", Input)

            inp.value = "/cl"
            await pilot.pause()

            assert overlay.visible is True
            names = [name for name, _ in overlay.filtered]
            assert "/clear" in names, names
            assert "/new" not in names, names


@pytest.mark.asyncio
async def test_overlay_dismisses_when_input_drops_leading_slash() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.screen.query_one(SlashCommandOverlay)
            inp = app.screen.query_one("#focus-input", Input)
            inp.value = "/h"
            await pilot.pause()
            assert overlay.visible is True

            inp.value = "h"
            await pilot.pause()
            assert overlay.visible is False, (
                "overlay must hide once input no longer starts with `/`"
            )


@pytest.mark.asyncio
async def test_enter_inserts_highlighted_command_into_input() -> None:
    from openminion.cli.interactive.widgets import FocusComposer

    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.screen.query_one(SlashCommandOverlay)
            inp = app.screen.query_one("#focus-input", Input)
            inp.value = "/cl"
            await pilot.pause()
            assert overlay.visible is True
            highlighted = overlay.selected()
            assert highlighted == "/clear"

            app.screen.on_focus_composer_submitted(FocusComposer.Submitted(text="/cl"))
            await pilot.pause()

            assert inp.value.startswith("/clear "), (
                f"Enter should insert the highlighted command + space; "
                f"got {inp.value!r}"
            )
            assert overlay.visible is False, (
                "overlay should hide after the user accepts a command"
            )


@pytest.mark.asyncio
async def test_escape_dismisses_overlay_without_inserting() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.screen.query_one(SlashCommandOverlay)
            inp = app.screen.query_one("#focus-input", Input)
            inp.value = "/cl"
            await pilot.pause()
            assert overlay.visible is True

            app.screen.action_handle_escape()
            await pilot.pause()
            assert overlay.visible is False
            assert inp.value == "/cl", "Esc must NOT modify the buffered command text"


@pytest.mark.asyncio
async def test_overlay_opens_when_multiline_editor_starts_with_slash() -> None:
    from textual.widgets import TextArea

    from openminion.cli.interactive.widgets import FocusComposer

    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            bar = app.screen.query_one(FocusComposer)
            bar.toggle_multiline()
            await pilot.pause()
            assert bar._multiline is True, "test setup: multiline should be on"

            overlay = app.screen.query_one(SlashCommandOverlay)
            editor = app.screen.query_one("#focus-editor", TextArea)
            assert overlay.visible is False

            editor.text = "/he"
            await pilot.pause()
            await pilot.pause()
            assert overlay.visible is True, (
                "overlay must show when multiline editor begins with `/`"
            )
            names = [name for name, _ in overlay.filtered]
            assert "/help" in names, (
                f"overlay should narrow to `/help` for `/he` prefix; got {names}"
            )

            editor.text = "he"
            await pilot.pause()
            await pilot.pause()
            assert overlay.visible is False


@pytest.mark.asyncio
async def test_overlay_enter_inserts_into_active_editor_in_multiline_mode() -> None:
    from textual.widgets import TextArea

    from openminion.cli.interactive.widgets import FocusComposer

    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            bar = app.screen.query_one(FocusComposer)
            bar.toggle_multiline()
            await pilot.pause()

            overlay = app.screen.query_one(SlashCommandOverlay)
            editor = app.screen.query_one("#focus-editor", TextArea)
            editor.text = "/he"
            await pilot.pause()
            await pilot.pause()
            assert overlay.visible is True
            assert overlay.selected() == "/help"

            app.screen.on_focus_composer_submitted(FocusComposer.Submitted(text="/he"))
            await pilot.pause()

            assert editor.text.startswith("/help "), (
                f"Enter must insert into TextArea in multiline mode; "
                f"got {editor.text!r}"
            )
            assert overlay.visible is False


@pytest.mark.asyncio
async def test_exact_slash_command_executes_on_first_enter() -> None:
    from openminion.cli.interactive.widgets import FocusTranscript

    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()

            await pilot.press("/", "h", "e", "l", "p")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            overlay = app.screen.query_one(SlashCommandOverlay)
            inp = app.screen.query_one("#focus-input", Input)
            assert inp.value == ""
            assert overlay.visible is False
            transcript = app.screen.query_one(FocusTranscript)
            assert any(
                "Slash commands:" in str(getattr(message, "body", ""))
                for message in transcript._messages
            )


@pytest.mark.asyncio
async def test_overlay_navigation_via_move_highlight() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.screen.query_one(SlashCommandOverlay)
            inp = app.screen.query_one("#focus-input", Input)
            inp.value = "/"
            await pilot.pause()

            assert overlay.filtered, "expected non-empty filtered list on bare `/`"
            initial = overlay.highlighted_index
            overlay.move_highlight(1)
            await pilot.pause()
            assert overlay.highlighted_index != initial

            overlay.move_highlight(len(overlay.filtered))
            await pilot.pause()
            assert 0 <= overlay.highlighted_index < len(overlay.filtered)
