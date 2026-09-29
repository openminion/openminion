from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.utils import is_dumb_terminal
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from openminion import __version__
from openminion.base.config.env import resolve_environment_config
from openminion.cli.presentation.header import (
    format_runtime_permission_posture,
    shorten_working_dir,
)
from openminion.cli.presentation.markers import token_rich_style
from openminion.cli.presentation.styles import is_color_enabled
from openminion.cli.presentation.styles import StyleToken
from openminion.cli.presentation.models import ChatMessage, MessageKind
from openminion.cli.interactive.terminal.transcript import TerminalTranscript

from .labels import _runtime_label


def build_terminal_console(
    console_factory: Callable[..., Console] = Console,
) -> Console:
    if is_color_enabled():
        return console_factory(
            force_terminal=True,
            color_system="truecolor",
            no_color=False,
        )
    console = console_factory()
    console.no_color = True
    return console


def configured_editor(runtime: Any) -> str:
    runtime_config = getattr(getattr(runtime, "config", None), "runtime", None)
    environment = resolve_environment_config(
        runtime_env=getattr(runtime_config, "env", None)
    )
    return str(environment.get("VISUAL") or environment.get("EDITOR") or "")


def show_response_time_enabled(env: Any | None = None) -> bool:
    return bool(resolve_environment_config(env=env).openminion_show_response_time)


def push_greeter(console: Console, *, runtime: Any, working_dir: str) -> None:
    muted = token_rich_style(StyleToken.MUTED)
    system = token_rich_style(StyleToken.SYSTEM)
    body_lines = [
        Text.assemble(
            ("OpenMinion CLI", token_rich_style(StyleToken.INFO, bold=True)),
            ("  ", ""),
            (f"(v{__version__})", muted),
        ),
        Text.assemble(
            ("interactive terminal", system),
            ("  ·  ", muted),
            ("type-ahead queue enabled", muted),
        ),
        Text(""),
        Text.assemble(("model:       ", muted), (_runtime_label(runtime), system)),
        Text.assemble(
            ("directory:   ", muted),
            (shorten_working_dir(working_dir) or working_dir or ".", system),
        ),
        Text.assemble(
            ("permissions: ", muted),
            (format_runtime_permission_posture(runtime), system),
        ),
    ]
    project_context = getattr(runtime, "project_context", None)
    if project_context is not None:
        body_lines.append(
            Text.assemble(
                ("context:     ", muted),
                (f"{project_context.display_name}", system),
                ("  ", ""),
                (f"({project_context.size_bytes} bytes)", muted),
            )
        )
    console.print()
    console.print(
        Panel(
            Text("\n").join(body_lines),
            border_style="dim",
            padding=(0, 1),
            expand=False,
        )
    )
    guidance = token_rich_style(StyleToken.SYSTEM)
    console.print(
        Text(
            "Tip: / for commands · @ to mention a file · keep typing while a turn runs",
            style=guidance,
        )
    )
    if project_context is not None and not bool(
        getattr(project_context, "is_canonical_name", False)
    ):
        console.print(
            Text(
                f"loaded project context from {project_context.display_name}; "
                "OpenMinion-native filename: OPENMINION.md",
                style=guidance,
            )
        )
    console.print()


async def emit_startup_notice(
    startup_notice: Callable[[], str],
    *,
    transcript: TerminalTranscript,
    prompt_session: PromptSession[str] | None = None,
) -> None:
    try:
        notice = await asyncio.to_thread(startup_notice)
    except (OSError, RuntimeError, TypeError, ValueError):
        return
    notice = str(notice or "").strip()
    if not notice:
        return
    if prompt_session is not None and not is_dumb_terminal():
        while not prompt_session.app.is_running:
            await asyncio.sleep(0)
    transcript.push_message(
        ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=notice)
    )


def schedule_startup_notice(
    startup_notice: Callable[[], str] | None,
    *,
    transcript: TerminalTranscript,
    prompt_session: PromptSession[str] | None = None,
) -> asyncio.Task[None] | None:
    if startup_notice is None:
        return None
    return asyncio.create_task(
        emit_startup_notice(
            startup_notice,
            transcript=transcript,
            prompt_session=prompt_session,
        )
    )


def cancel_startup_notice(task: asyncio.Task[None] | None) -> None:
    if task is not None and not task.done():
        task.cancel()


__all__ = [
    "build_terminal_console",
    "cancel_startup_notice",
    "configured_editor",
    "emit_startup_notice",
    "push_greeter",
    "schedule_startup_notice",
    "show_response_time_enabled",
]
