from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from textual.css.query import QueryError
from textual.widgets import Input

from openminion.cli.tui.focus.app import FocusApp, _DemoFocusRuntime
from openminion.cli.tui.focus.input import InputStateMixin
from openminion.cli.tui.focus.widgets import (
    FileMentionOverlay,
    SlashCommandOverlay,
)
from openminion.cli.tui.focus.widgets import FocusComposer


def _seed_workspace(root: Path) -> None:
    (root / "README.md").write_text("readme")
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "alpha.py").write_text("a")
    (root / "src" / "beta.py").write_text("b")
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "test_alpha.py").write_text("t")


def _make_app(tmp: str) -> FocusApp:
    runtime = _DemoFocusRuntime(working_dir=tmp)
    return FocusApp(runtime=runtime, working_dir=tmp)


class _BrokenInputState(InputStateMixin):
    _suppress_slash_overlay_once = False
    _suppress_file_overlay_once = False

    def query_one(self, *args, **kwargs):
        raise QueryError("missing widget")


def test_input_state_overlay_helpers_tolerate_missing_widgets() -> None:
    state = _BrokenInputState()

    assert state._slash_overlay() is None
    assert state._file_overlay() is None
    assert state._active_editor_value_and_cursor() == ("", 0)


@pytest.mark.asyncio
async def test_typing_at_opens_file_overlay_seeded_from_working_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _seed_workspace(Path(tmp))
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.screen.query_one(FileMentionOverlay)
            assert overlay.visible is False, "overlay starts hidden"

            inp = app.screen.query_one("#focus-input", Input)
            inp.value = "@"
            inp.cursor_position = 1
            await pilot.pause()
            await pilot.pause()

            assert overlay.visible is True, "@ must open the file overlay"
            relatives = [rel for rel, _ in overlay.filtered]
            assert "README.md" in relatives
            assert "src/alpha.py" in relatives
            assert "src/beta.py" in relatives
            assert "tests/test_alpha.py" in relatives


@pytest.mark.asyncio
async def test_typing_at_prefix_narrows_overlay() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _seed_workspace(Path(tmp))
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.screen.query_one(FileMentionOverlay)
            inp = app.screen.query_one("#focus-input", Input)

            inp.value = "@src"
            inp.cursor_position = 4
            await pilot.pause()
            await pilot.pause()

            relatives = [rel for rel, _ in overlay.filtered]
            # Tier-1 (prefix `src`): src/alpha.py, src/beta.py
            assert "src/alpha.py" in relatives
            assert "src/beta.py" in relatives
            # README.md doesn't start with or contain `src`.
            assert "README.md" not in relatives


@pytest.mark.asyncio
async def test_enter_replaces_active_token_at_start_of_input() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _seed_workspace(Path(tmp))
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.screen.query_one(FileMentionOverlay)
            inp = app.screen.query_one("#focus-input", Input)

            inp.value = "@src"
            inp.cursor_position = 4
            await pilot.pause()
            await pilot.pause()
            assert overlay.visible
            chosen = overlay.selected()
            assert chosen, "expected a highlighted candidate after typing @src"

            # Drive the same submit path the Enter binding uses.
            app.screen.on_focus_composer_submitted(
                FocusComposer.Submitted(text=inp.value)
            )
            await pilot.pause()

            assert inp.value == f"{chosen} ", (
                f"start-of-input replacement should produce `{chosen} `; "
                f"got {inp.value!r}"
            )
            assert overlay.visible is False


@pytest.mark.asyncio
async def test_enter_replaces_active_token_mid_text_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _seed_workspace(Path(tmp))
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.screen.query_one(FileMentionOverlay)
            inp = app.screen.query_one("#focus-input", Input)

            prefix = "please review "
            value = f"{prefix}@src"
            inp.value = value
            inp.cursor_position = len(value)
            await pilot.pause()
            await pilot.pause()
            assert overlay.visible
            chosen = overlay.selected()
            assert chosen

            app.screen.on_focus_composer_submitted(FocusComposer.Submitted(text=value))
            await pilot.pause()

            assert inp.value == f"{prefix}{chosen} ", (
                f"mid-text replacement should preserve prefix; got {inp.value!r}"
            )


@pytest.mark.asyncio
async def test_escape_dismisses_overlay_without_modifying_input() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _seed_workspace(Path(tmp))
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.screen.query_one(FileMentionOverlay)
            inp = app.screen.query_one("#focus-input", Input)
            inp.value = "@sr"
            inp.cursor_position = 3
            await pilot.pause()
            await pilot.pause()
            assert overlay.visible

            app.screen.action_handle_escape()
            await pilot.pause()
            assert overlay.visible is False
            assert inp.value == "@sr", "Escape must not modify typed text"


@pytest.mark.asyncio
async def test_slash_and_file_overlays_are_mutually_exclusive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _seed_workspace(Path(tmp))
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            file_overlay = app.screen.query_one(FileMentionOverlay)
            slash_overlay = app.screen.query_one(SlashCommandOverlay)
            inp = app.screen.query_one("#focus-input", Input)

            # `/` triggers slash, NOT file.
            inp.value = "/"
            inp.cursor_position = 1
            await pilot.pause()
            await pilot.pause()
            assert slash_overlay.visible is True
            assert file_overlay.visible is False

            # `@` triggers file, NOT slash.
            inp.value = "@"
            inp.cursor_position = 1
            await pilot.pause()
            await pilot.pause()
            assert file_overlay.visible is True
            assert slash_overlay.visible is False

            # Plain text triggers neither.
            inp.value = "hello"
            inp.cursor_position = 5
            await pilot.pause()
            await pilot.pause()
            assert slash_overlay.visible is False
            assert file_overlay.visible is False


@pytest.mark.asyncio
async def test_email_address_does_not_trigger_file_overlay() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _seed_workspace(Path(tmp))
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.screen.query_one(FileMentionOverlay)
            inp = app.screen.query_one("#focus-input", Input)

            inp.value = "send to alice@example.com"
            inp.cursor_position = len(inp.value)
            await pilot.pause()
            await pilot.pause()

            assert overlay.visible is False, (
                "email-style `@` should NOT activate the file overlay"
            )


@pytest.mark.asyncio
async def test_overlay_works_in_multiline_mode() -> None:
    from textual.widgets import TextArea

    with tempfile.TemporaryDirectory() as tmp:
        _seed_workspace(Path(tmp))
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            bar = app.screen.query_one(FocusComposer)
            bar.toggle_multiline()
            await pilot.pause()
            assert bar._multiline is True

            overlay = app.screen.query_one(FileMentionOverlay)
            editor = app.screen.query_one("#focus-editor", TextArea)
            editor.text = "@src"
            # Mirror real-typing cursor placement at end of buffer.
            try:
                editor.move_cursor((0, len("@src")))
            except Exception:
                pass
            await pilot.pause()
            await pilot.pause()
            assert overlay.visible is True, "overlay must work in multiline"

            relatives = [rel for rel, _ in overlay.filtered]
            assert "src/alpha.py" in relatives
            assert "src/beta.py" in relatives


@pytest.mark.asyncio
async def test_multiline_enter_replaces_active_token_in_textarea() -> None:
    from textual.widgets import TextArea

    with tempfile.TemporaryDirectory() as tmp:
        _seed_workspace(Path(tmp))
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            bar = app.screen.query_one(FocusComposer)
            bar.toggle_multiline()
            await pilot.pause()

            overlay = app.screen.query_one(FileMentionOverlay)
            editor = app.screen.query_one("#focus-editor", TextArea)
            editor.text = "review @src"
            try:
                editor.move_cursor((0, len("review @src")))
            except Exception:
                pass
            await pilot.pause()
            await pilot.pause()
            assert overlay.visible
            chosen = overlay.selected()
            assert chosen

            app.screen.on_focus_composer_submitted(
                FocusComposer.Submitted(text="review @src")
            )
            await pilot.pause()

            assert editor.text == f"review {chosen} ", (
                f"multiline replacement should write to TextArea; got {editor.text!r}"
            )
            assert overlay.visible is False
