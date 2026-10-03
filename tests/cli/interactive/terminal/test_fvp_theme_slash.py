from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import MagicMock

from rich.console import Console
from rich.text import Text

from openminion.cli.interactive.terminal.shell import (
    _SLASH_COMMANDS,
    _build_terminal_console,
    _handle_slash,
)


def _dispatch(text: str, *, runtime: object | None = None) -> str:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    asyncio.run(
        _handle_slash(
            text,
            runtime=runtime or SimpleNamespace(),
            console=console,
            transcript=MagicMock(),
            overlay=MagicMock(),
            status_line=MagicMock(),
            working_dir="/tmp",
        )
    )
    return buf.getvalue()


def test_theme_in_slash_catalog() -> None:
    assert "/theme" in _SLASH_COMMANDS


def test_explicit_color_on_forces_console_color_with_dumb_term(monkeypatch) -> None:
    from openminion.cli.presentation import styles

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    styles.set_color_mode("always")
    try:
        console = _build_terminal_console()
        assert console.is_terminal is True
        assert console.color_system == "truecolor"
        buffer = io.StringIO()
        console.file = buffer
        console.print(Text("running", style="yellow"))
        assert "\x1b[33m" in buffer.getvalue()
    finally:
        styles.set_color_mode(None)


def test_explicit_color_never_disables_console_color() -> None:
    from openminion.cli.presentation import styles

    styles.set_color_mode("never")
    try:
        assert _build_terminal_console().no_color is True
    finally:
        styles.set_color_mode(None)


def test_bare_theme_shows_active_and_available() -> None:
    out = _dispatch("/theme")
    assert "Active Theme:" in out
    assert "/theme list" in out
    assert "dark" in out.lower()


def test_bare_theme_shows_switch_hint() -> None:
    out = _dispatch("/theme")
    assert "/theme <name>" in out
    assert "/theme save <name>" in out


def test_theme_switch_to_light_succeeds() -> None:
    from openminion.cli.presentation.styles import (
        get_active_theme_name,
        set_active_theme,
    )
    from openminion.cli.theme import DARK

    initial = get_active_theme_name()
    try:
        out = _dispatch("/theme light")
        assert "active theme is now 'light'" in out
        assert get_active_theme_name() == "light"
    finally:
        if initial == "dark":
            set_active_theme(DARK)


def test_theme_switch_to_dark_succeeds() -> None:
    from openminion.cli.presentation.styles import (
        get_active_theme_name,
        set_active_theme,
    )
    from openminion.cli.theme import DARK, LIGHT

    initial = get_active_theme_name()
    try:
        # First set to light, then switch back to dark.
        set_active_theme(LIGHT)
        out = _dispatch("/theme dark")
        assert "active theme is now 'dark'" in out
        assert get_active_theme_name() == "dark"
    finally:
        if initial == "dark":
            set_active_theme(DARK)
        else:
            set_active_theme(LIGHT)


def test_theme_switch_unknown_theme_surfaces_error() -> None:
    out = _dispatch("/theme neon")
    assert "unknown theme" in out
    assert "neon" in out
    assert "light" in out
    assert "dark" in out


def test_theme_switch_is_case_insensitive() -> None:
    from openminion.cli.presentation.styles import set_active_theme
    from openminion.cli.theme import DARK

    try:
        out = _dispatch("/theme LIGHT")
        assert "active theme is now 'light'" in out
    finally:
        set_active_theme(DARK)


def test_theme_variant_switch_to_balanced() -> None:
    out = _dispatch("/theme variant balanced")
    assert "variant" in out.lower()
    assert "balanced" in out


def test_theme_variant_switch_to_high_contrast() -> None:
    out = _dispatch("/theme variant high_contrast")
    assert "high_contrast" in out


def test_theme_variant_unknown_surfaces_error() -> None:
    out = _dispatch("/theme variant neon")
    assert "unknown variant" in out
    assert "neon" in out


def test_theme_variant_missing_arg_surfaces_error() -> None:
    out = _dispatch("/theme variant")
    assert "unknown variant" in out


def test_theme_save_persists_through_existing_selection_owner(tmp_path) -> None:
    from openminion.cli.presentation.styles import set_active_theme
    from openminion.cli.theme import DARK
    from openminion.cli.theme import read_persisted_theme

    runtime = SimpleNamespace(_rt=SimpleNamespace(data_root=tmp_path))
    try:
        out = _dispatch("/theme save light", runtime=runtime)

        assert "theme saved to" in out
        assert read_persisted_theme(tmp_path) == "light"
    finally:
        set_active_theme(DARK)


def test_invalid_theme_save_writes_nothing(tmp_path) -> None:
    runtime = SimpleNamespace(_rt=SimpleNamespace(data_root=tmp_path))

    out = _dispatch("/theme save neon", runtime=runtime)

    assert "unknown theme" in out
    assert not (tmp_path / "cli" / "theme.json").exists()


def test_composer_and_shared_messages_follow_live_theme_switch() -> None:
    from openminion.cli.interactive.terminal.composer import TerminalComposer
    from openminion.cli.presentation.messages import (
        render_error_text,
        render_system_text,
        render_user_text,
    )
    from openminion.cli.presentation.styles import set_active_theme, set_color_mode
    from openminion.cli.theme import DARK, LIGHT

    set_color_mode("always")
    set_active_theme(DARK)
    try:
        composer = TerminalComposer(color=True)
        dark_rules = dict(composer._session.style._style_rules)

        set_active_theme(LIGHT)
        composer.apply_theme()
        light_rules = dict(composer._session.style._style_rules)

        assert DARK.surface_panel_bg in dark_rules["bottom-toolbar"]
        assert DARK.text_secondary in dark_rules["bottom-toolbar"]
        assert DARK.surface_app_bg in dark_rules["placeholder"]
        assert DARK.text_secondary in dark_rules["placeholder"]
        assert "noreverse" in dark_rules["placeholder"]
        assert "italic" not in dark_rules["placeholder"]
        assert LIGHT.surface_panel_bg in light_rules["bottom-toolbar"]
        user_text = render_user_text("hello")
        assert user_text.plain == "❯ hello"
        assert LIGHT.text_accent in str(user_text.spans[0].style)
        assert "dim" in str(user_text.spans[0].style)
        assert LIGHT.text_secondary in str(user_text.spans[1].style)
        assert "dim" not in str(user_text.spans[1].style)
        assert LIGHT.text_muted in str(render_system_text("notice").style)
        assert LIGHT.state_error in str(render_error_text("failure").style)
    finally:
        set_active_theme(DARK)
        set_color_mode(None)


def test_shell_outputs_follow_live_theme_switch() -> None:
    from openminion.cli.interactive.terminal.shell.actions import (
        _handle_slash_expand,
    )
    from openminion.cli.interactive.terminal.shell import _build_ctrl_o_handler
    from openminion.cli.interactive.terminal.shell.delegation import (
        handle_slash_delegate,
    )
    from openminion.cli.interactive.terminal.shell.model_setup import _cancel
    from openminion.cli.interactive.terminal.shell.project import run_slash_goal
    from openminion.cli.interactive.terminal.shell.renderers import (
        _render_sessions_list,
    )
    from openminion.cli.interactive.terminal.shell.sessions import start_new_session
    from openminion.cli.interactive.terminal.shell.slash_output import (
        copy_latest_message,
    )
    from openminion.cli.presentation.styles import set_active_theme, set_color_mode
    from openminion.cli.theme import DARK, LIGHT

    def rendered_style(render) -> str:
        console = MagicMock()
        render(console)
        return str(console.print.call_args.args[0].style)

    transcript = MagicMock()
    transcript.copy_last_copyable_message.return_value = ""
    set_color_mode("always")
    set_active_theme(LIGHT)
    try:
        error_outputs = [
            rendered_style(
                lambda console: run_slash_goal(
                    "/goal",
                    runtime=SimpleNamespace(),
                    console=console,
                    status_line=MagicMock(),
                )
            ),
            rendered_style(
                lambda console: handle_slash_delegate(
                    "/delegate", runtime=SimpleNamespace(), console=console
                )
            ),
            rendered_style(
                lambda console: _handle_slash_expand(
                    "/expand nope", transcript=transcript, console=console
                )
            ),
        ]
        muted_outputs = [
            rendered_style(
                lambda console: _build_ctrl_o_handler(
                    transcript=transcript,
                    console=console,
                )()
            ),
            rendered_style(
                lambda console: start_new_session(
                    runtime=SimpleNamespace(),
                    console=console,
                    transcript=transcript,
                )
            ),
            rendered_style(_cancel),
            rendered_style(lambda console: copy_latest_message(transcript, console)),
            rendered_style(
                lambda console: _render_sessions_list(
                    runtime=SimpleNamespace(), console=console
                )
            ),
        ]

        assert all(LIGHT.state_error in style for style in error_outputs)
        assert all(LIGHT.text_muted in style for style in muted_outputs)
        assert all(DARK.state_error not in style for style in error_outputs)
        assert all(DARK.text_muted not in style for style in muted_outputs)
    finally:
        set_active_theme(DARK)
        set_color_mode(None)


def test_shell_outputs_remain_plain_when_color_is_disabled() -> None:
    from openminion.cli.interactive.terminal.shell.actions import (
        _handle_slash_expand,
    )
    from openminion.cli.presentation.styles import set_color_mode

    console = MagicMock()
    set_color_mode("never")
    try:
        _handle_slash_expand(
            "/expand nope",
            transcript=MagicMock(),
            console=console,
        )
        style = str(console.print.call_args.args[0].style)
        assert "#" not in style
    finally:
        set_color_mode(None)
