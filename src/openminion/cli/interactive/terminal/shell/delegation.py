from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.text import Text

from openminion.cli.commands.agent_delegation import (
    render_agent_delegate_result,
    request_from_slash_args,
)
from openminion.cli.presentation.markers import token_rich_style
from openminion.cli.presentation.styles import StyleToken

_ERR_STYLE = token_rich_style(StyleToken.ERROR)
_SYSTEM_STYLE = token_rich_style(StyleToken.SYSTEM)


def handle_slash_delegate(
    text: str,
    *,
    runtime: Any,
    console: Console,
    approval_callback: Callable[[str, dict[str, Any], Any], Any] | None = None,
) -> None:
    runner = getattr(runtime, "delegate_task", None)
    if not callable(runner):
        console.print(
            Text("(/delegate: runtime does not expose delegation)", style=_ERR_STYLE)
        )
        return
    arg = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
    try:
        request = request_from_slash_args(arg)
    except ValueError as exc:
        console.print(Text(str(exc), style=_ERR_STYLE))
        return
    result = runner(
        mode=request.mode,
        target_agent_id=request.target_agent_id,
        instruction=request.instruction,
        task_id=request.task_id,
        timeout_seconds=request.timeout_seconds,
        approval_callback=approval_callback,
    )
    console.print(
        Text(render_agent_delegate_result(dict(result or {})), style=_SYSTEM_STYLE)
    )


__all__ = ["handle_slash_delegate"]
