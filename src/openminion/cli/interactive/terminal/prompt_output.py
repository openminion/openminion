import io
from collections.abc import Callable
from typing import Any

from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.application.current import get_app_or_none, set_app
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.renderer import print_formatted_text
from prompt_toolkit.styles import Style
from rich.console import Console


def write_console_render_via_prompt_output(
    *,
    console: Console,
    prompt_output: Any,
    render: Callable[[], None],
) -> None:
    buffer = io.StringIO()
    original_file = console.file
    original_force_terminal = getattr(console, "_force_terminal", None)
    try:
        console.file = buffer
        console._force_terminal = True
        render()
    finally:
        console.file = original_file
        console._force_terminal = original_force_terminal
    payload = buffer.getvalue()
    if not payload:
        return
    print_formatted_text(prompt_output, ANSI(payload), Style([]))


def write_terminal_control_via_prompt_output(
    *, prompt_output: Any, payload: str
) -> None:
    writer = getattr(prompt_output, "write_raw", None)
    if not callable(writer):
        return
    writer(str(payload or ""))
    flusher = getattr(prompt_output, "flush", None)
    if callable(flusher):
        flusher()


def build_prompt_safe_terminal_writer(
    *,
    console: Console,
    prompt_session: Any,
) -> Callable[[Callable[[], None]], Any]:
    transcript_cursor_saved = False

    def _hide_cursor_while_prompt_is_suspended() -> None:
        prompt_output = getattr(prompt_session, "output", None)
        hide_cursor = getattr(prompt_output, "hide_cursor", None)
        if not callable(hide_cursor):
            return
        hide_cursor()
        flush = getattr(prompt_output, "flush", None)
        if callable(flush):
            flush()

    def _run_with_prompt(render: Callable[[], None]) -> Any:
        app = getattr(prompt_session, "app", None)
        if bool(getattr(app, "is_running", False)):
            # ``run_in_terminal`` erases the prompt before writing above it.
            # Hide the cursor inside the suspended callback, after that erase,
            # so a redraw between scheduling and execution cannot reveal the
            # transient output cursor before the final draft redraw.
            def _render_with_hidden_cursor() -> None:
                _hide_cursor_while_prompt_is_suspended()
                render()

            if get_app_or_none() is app:
                return run_in_terminal(
                    _render_with_hidden_cursor,
                    render_cli_done=False,
                )
            with set_app(app):
                return run_in_terminal(
                    _render_with_hidden_cursor,
                    render_cli_done=False,
                )
        render()
        return None

    def _writer(render: Callable[[], None]) -> Any:
        prompt_output = getattr(prompt_session, "output", None)
        if prompt_output is None:
            return _run_with_prompt(render)

        def _render_at_transcript_cursor() -> None:
            if transcript_cursor_saved:
                write_terminal_control_via_prompt_output(
                    prompt_output=prompt_output,
                    payload="\x1b8",
                )
            write_console_render_via_prompt_output(
                console=console,
                prompt_output=prompt_output,
                render=render,
            )
            if transcript_cursor_saved:
                write_terminal_control_via_prompt_output(
                    prompt_output=prompt_output,
                    payload="\x1b7",
                )

        return _run_with_prompt(_render_at_transcript_cursor)

    def _remember_cursor() -> None:
        nonlocal transcript_cursor_saved
        prompt_output = getattr(prompt_session, "output", None)
        if prompt_output is None:
            return
        write_terminal_control_via_prompt_output(
            prompt_output=prompt_output,
            payload="\x1b7",
        )
        transcript_cursor_saved = True

    def _write_control(payload: str) -> Any:
        prompt_output = getattr(prompt_session, "output", None)
        if prompt_output is None:
            return None
        return _run_with_prompt(
            lambda: write_terminal_control_via_prompt_output(
                prompt_output=prompt_output,
                payload=payload,
            )
        )

    _writer.write_control = _write_control  # type: ignore[attr-defined]
    _writer.remember_cursor = _remember_cursor  # type: ignore[attr-defined]
    return _writer
