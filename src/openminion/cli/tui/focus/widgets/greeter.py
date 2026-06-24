from __future__ import annotations

import re
from typing import Any

from openminion.base.config.env import resolve_environment_config
from openminion.cli.constants import (
    OPENMINION_FOCUS_EXAMPLE_PROMPTS_ENV,
    OPENMINION_FOCUS_GREETING_ENV,
)
from openminion.cli.tui.presentation.header import shorten_working_dir
from openminion.cli.tui.presentation.models import ChatMessage, MessageKind


_DEFAULT_GREETING = "How can I help today?"
_DEFAULT_EXAMPLE_PROMPTS: tuple[str, ...] = (
    "explain this codebase",
    "find all references to <symbol>",
    "add tests for <file>",
)
_KEY_HINT = "/help for commands · @ to mention a file · Ctrl+P palette"


def build_greeter_message(
    *,
    runtime: Any,
    working_dir: str,
    theme_name: str,
) -> ChatMessage:
    """Return the greeter as a `MessageKind.SYSTEM` message."""
    env = resolve_environment_config()
    greeting = (
        str(env.get(OPENMINION_FOCUS_GREETING_ENV, "") or "").strip()
        or _DEFAULT_GREETING
    )
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
    raw_examples = str(env.get(OPENMINION_FOCUS_EXAMPLE_PROMPTS_ENV, "") or "").strip()
    if raw_examples:
        parts = [part.strip() for part in re.split(r"[;\n]", raw_examples)]
        examples = tuple(part for part in parts if part) or _DEFAULT_EXAMPLE_PROMPTS
    else:
        examples = _DEFAULT_EXAMPLE_PROMPTS

    lines = [
        greeting,
        f"  {cwd_short} · {agent_name}/{runtime_label} · theme: {theme_label}",
    ]
    project_context = getattr(runtime, "project_context", None)
    if project_context is not None:
        lines.append(
            f"  context: {project_context.display_name} ({project_context.size_bytes} bytes)"
        )
    lines.extend(["", "Try:"])
    for example in examples:
        lines.append(f"  {example}")
    lines.append("")
    lines.append(_KEY_HINT)
    if project_context is not None and not bool(
        getattr(project_context, "is_canonical_name", False)
    ):
        lines.append(
            f"found {project_context.display_name}; consider renaming to OPENMINION.md for canonical support"
        )

    body = "\n".join(lines)
    return ChatMessage(
        kind=MessageKind.SYSTEM,
        sender="system",
        body=body,
        show_header=False,
    )


__all__ = ["build_greeter_message"]
