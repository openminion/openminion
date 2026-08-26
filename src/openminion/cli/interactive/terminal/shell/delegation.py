import asyncio
from collections.abc import Callable
import inspect
from typing import Any

from rich.console import Console
from rich.text import Text

from openminion.cli.commands.agent.delegation import (
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


async def run_slash_delegate(
    text: str,
    runtime: Any,
    console: Console,
    approval_callback: Callable[[str, dict[str, Any], Any], Any] | None,
) -> None:
    terminal_loop = asyncio.get_running_loop()
    delegated_approval_callback = approval_callback
    if approval_callback is not None:

        async def invoke_approval(
            tool_name: str, args: dict[str, Any], call_id: Any
        ) -> bool:
            result = approval_callback(tool_name, args, call_id)
            return bool(await result if inspect.isawaitable(result) else result)

        def approval_from_worker(
            tool_name: str, args: dict[str, Any], call_id: Any
        ) -> bool:
            return asyncio.run_coroutine_threadsafe(
                invoke_approval(tool_name, args, call_id), terminal_loop
            ).result()

        delegated_approval_callback = approval_from_worker
    await asyncio.to_thread(
        handle_slash_delegate,
        text,
        runtime=runtime,
        console=console,
        approval_callback=delegated_approval_callback,
    )


__all__ = ["handle_slash_delegate", "run_slash_delegate"]
