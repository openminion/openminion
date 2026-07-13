from __future__ import annotations

from typing import Any

from openminion import __version__
from openminion.cli.presentation.header import shorten_working_dir
from openminion.cli.presentation.models import ChatMessage, MessageKind


def build_welcome_message(
    *,
    runtime: Any,
    working_dir: str,
    theme_name: str,
) -> ChatMessage:
    cwd_short = shorten_working_dir(str(working_dir or "")) or "."
    agent_name = str(getattr(runtime, "agent_id", "") or "").strip() or "(unbound)"
    provider = str(getattr(runtime, "provider_name", "") or "").strip()
    model = str(getattr(runtime, "model_name", "") or "").strip()
    if provider and model:
        runtime_label = f"{provider}/{model}"
    elif model:
        runtime_label = model
    else:
        runtime_label = "(no model)"
    theme_label = str(theme_name or "").strip().lower() or "dark"

    lines = [
        f"OpenMinion focus — single-agent shell  v{__version__}",
        f"  cwd: {cwd_short}",
        f"  agent: {agent_name}   model: {runtime_label}   theme: {theme_label}",
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
