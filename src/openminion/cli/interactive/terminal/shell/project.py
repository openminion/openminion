from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.text import Text

from openminion.cli.presentation.styles import StyleToken
from openminion.cli.presentation.markers import token_rich_style

_ERR_STYLE = token_rich_style(StyleToken.ERROR)
_SYSTEM_STYLE = token_rich_style(StyleToken.SYSTEM)


async def run_slash_project(
    text: str,
    *,
    runtime: Any,
    console: Console,
    approval_callback: Callable[[str, dict[str, Any], Any], Any] | None,
) -> None:
    if approval_callback is None:
        console.print(Text("(/project: approval is unavailable)", style=_ERR_STYLE))
        return
    try:
        request = runtime.prepare_project_command(text)
        approved = bool(
            await approval_callback(
                "project.start",
                runtime.project_launch_approval_args(request),
                request.run.run_id,
            )
        )
        tone, body = (
            runtime.launch_prepared_project(request)
            if approved
            else runtime.deny_prepared_project(request)
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        tone, body = ("error", f"/project failed: {exc}")
    console.print(Text(body, style=_ERR_STYLE if tone == "error" else _SYSTEM_STYLE))
