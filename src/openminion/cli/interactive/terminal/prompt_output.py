from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import ANSI
from rich.console import Console


def write_console_render_via_prompt_output(
    *,
    console: Console,
    prompt_output: Any,
    render: Callable[[], None],
) -> None:
    """Route a Rich render through prompt-toolkit's output adapter."""

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
    print_formatted_text(ANSI(payload), output=prompt_output, end="", flush=True)


def write_terminal_control_via_prompt_output(
    *, prompt_output: Any, payload: str
) -> None:
    """Write raw terminal control bytes through prompt-toolkit's output."""

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
    def _run_with_prompt(render: Callable[[], None]) -> Any:
        app = getattr(prompt_session, "app", None)
        runner = getattr(app, "run_in_terminal", None)
        if callable(runner):
            return runner(render, render_cli_done=False)
        render()
        return None

    def _writer(render: Callable[[], None]) -> Any:
        prompt_output = getattr(prompt_session, "output", None)
        wrapped = (
            (lambda: render())
            if prompt_output is None
            else (
                lambda: write_console_render_via_prompt_output(
                    console=console,
                    prompt_output=prompt_output,
                    render=render,
                )
            )
        )
        return _run_with_prompt(wrapped)

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
    return _writer
