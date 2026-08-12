from __future__ import annotations

from typing import Any

from openminion import __version__
from openminion.cli.presentation.header import (
    format_runtime_adapter,
    format_runtime_label,
    format_runtime_provider,
    shorten_working_dir,
)
from openminion.cli.presentation.models import ChatMessage, MessageKind


def build_welcome_message(
    *,
    runtime: Any,
    working_dir: str,
    theme_name: str,
) -> ChatMessage:
    cwd_short = shorten_working_dir(str(working_dir or "")) or "."
    agent_name = str(getattr(runtime, "agent_id", "") or "").strip() or "(unbound)"
    runtime_label = format_runtime_label(runtime)
    if runtime_label == "—":
        runtime_label = "(no model)"
    provider = format_runtime_provider(runtime)
    adapter = format_runtime_adapter(runtime)
    theme_label = str(theme_name or "").strip().lower() or "dark"

    lines = [
        f"OpenMinion CLI - single-agent interactive shell  v{__version__}",
        f"  cwd: {cwd_short}",
        f"  agent: {agent_name}   model: {runtime_label}   theme: {theme_label}",
        f"  provider: {provider}" + (f"   API adapter: {adapter}" if adapter else ""),
        "",
        "Tips:",
        "  /help       show all slash commands and key bindings",
        "  @<path>     mention a file from the working directory",
        "  Ctrl+P      open the command palette",
    ]
    body = "\n".join(lines)

    return ChatMessage(
        kind=MessageKind.SYSTEM,
        sender="system",
        body=body,
        show_header=False,
    )


__all__ = ["build_welcome_message"]
