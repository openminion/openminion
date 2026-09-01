import re
from typing import Any

from openminion.base.config.env import resolve_environment_config
from openminion.cli.constants import (
    OPENMINION_FOCUS_EXAMPLE_PROMPTS_ENV,
    OPENMINION_FOCUS_GREETING_ENV,
)
from openminion.cli.presentation.header import (
    format_runtime_adapter,
    format_runtime_label,
    format_runtime_provider,
    shorten_working_dir,
)
from openminion.cli.presentation.models import ChatMessage, MessageKind


_DEFAULT_GREETING = "OpenMinion"
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
    runtime_label = format_runtime_label(runtime)
    if runtime_label == "—":
        runtime_label = "(no model)"
    provider = format_runtime_provider(runtime)
    adapter = format_runtime_adapter(runtime)
    theme_label = str(theme_name or "").strip().lower() or "dark"
    raw_examples = str(env.get(OPENMINION_FOCUS_EXAMPLE_PROMPTS_ENV, "") or "").strip()
    if raw_examples:
        parts = [part.strip() for part in re.split(r"[;\n]", raw_examples)]
        examples = tuple(part for part in parts if part) or _DEFAULT_EXAMPLE_PROMPTS
    else:
        examples = _DEFAULT_EXAMPLE_PROMPTS

    lines = [
        greeting,
        "How can I help today?",
        f"  {cwd_short} · {agent_name}/{runtime_label} · theme: {theme_label}",
    ]
    connection = f"  provider: {provider}"
    if adapter:
        connection += f" · API adapter: {adapter}"
    lines.append(connection)
    project_context = getattr(runtime, "project_context", None)
    if project_context is not None:
        lines.append(
            f"  context: {project_context.display_name} ({project_context.size_bytes} bytes)"
        )
    lines.extend(["", "Try:"])
    for example in examples:
        lines.append(f"  {example}")
    lines.extend(("", _KEY_HINT))
    if project_context is not None and not bool(
        getattr(project_context, "is_canonical_name", False)
    ):
        lines.append(
            f"loaded project context from {project_context.display_name}; OpenMinion-native filename: OPENMINION.md"
        )

    body = "\n".join(lines)
    return ChatMessage(
        kind=MessageKind.SYSTEM,
        sender="system",
        body=body,
        show_header=False,
    )


__all__ = ["build_greeter_message"]
