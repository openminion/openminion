from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from difflib import get_close_matches


@dataclass(frozen=True)
class SlashCommandMetadata:
    name: str
    description: str
    aliases: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


SLASH_COMMANDS: tuple[SlashCommandMetadata, ...] = (
    SlashCommandMetadata("/init", "Create OPENMINION.md for this project"),
    SlashCommandMetadata("/new", "Start a new session", ("/new session",)),
    SlashCommandMetadata("/clear", "Clear chat history", ("/cls",)),
    SlashCommandMetadata("/sessions", "Pick a session", ("/session",)),
    SlashCommandMetadata("/participants", "Show room participants and routing"),
    SlashCommandMetadata("/invite", "Invite a room agent or human"),
    SlashCommandMetadata("/kick", "Remove a room participant"),
    SlashCommandMetadata("/activate", "Set the active room agent"),
    SlashCommandMetadata("/routing", "Show or set room routing"),
    SlashCommandMetadata("/resume", "Resume a prior session with messages"),
    SlashCommandMetadata("/agents", "List agents or inspect/switch one", ("/agent",)),
    SlashCommandMetadata("/delegate", "Delegate work or inspect a delegated task"),
    SlashCommandMetadata("/model", "Show or choose this agent's configured model"),
    SlashCommandMetadata("/theme", "Show, switch, save, or reset the active theme"),
    SlashCommandMetadata("/tools", "Show available tools", ("/tool",)),
    SlashCommandMetadata("/browser", "Show browser status or control tabs"),
    SlashCommandMetadata("/mcp", "Show configured MCP servers and tools"),
    SlashCommandMetadata("/cost", "Show live token usage and available cost"),
    SlashCommandMetadata("/tokens", "Show durable token usage details"),
    SlashCommandMetadata("/context", "Show visual context usage"),
    SlashCommandMetadata("/context-review", "Review memory and context evidence"),
    SlashCommandMetadata("/overview", "Show the read-only operations overview"),
    SlashCommandMetadata("/goal", "Create, bind, inspect, or run a session goal"),
    SlashCommandMetadata("/project", "Start a durable repository project"),
    SlashCommandMetadata("/effort", "Show or set per-turn effort"),
    SlashCommandMetadata("/memory", "Show memory health and inventory"),
    SlashCommandMetadata("/graph", "Show graph viewer commands"),
    SlashCommandMetadata("/tasks", "Show task inventory", ("/task",)),
    SlashCommandMetadata("/skills", "List skills or view one with /skills <skill_id>"),
    SlashCommandMetadata(
        "/statusline", "Show or set status line preset/custom command"
    ),
    SlashCommandMetadata("/undo", "Rewind latest turn or restore a file"),
    SlashCommandMetadata("/permissions", "Show or set sandbox approval mode"),
    SlashCommandMetadata("/diff", "Show workspace git diff"),
    SlashCommandMetadata("/review", "Review current or supplied diff"),
    SlashCommandMetadata("/compact", "Compact conversation history if supported"),
    SlashCommandMetadata("/queue", "Inspect or control queued type-ahead prompts"),
    SlashCommandMetadata("/copy", "Copy the latest message"),
    SlashCommandMetadata("/status", "Show agent / model / session / dir"),
    SlashCommandMetadata("/telemetry", "Show local telemetry diagnostics"),
    SlashCommandMetadata("/trace", "List or show local trace metadata"),
    SlashCommandMetadata("/quiet", "Hide tool blocks for the session"),
    SlashCommandMetadata("/normal", "Truncated tool blocks (default)"),
    SlashCommandMetadata("/verbose", "Show full tool block output"),
    SlashCommandMetadata("/details", "Toggle detailed tool blocks for the session"),
    SlashCommandMetadata("/expand", "Expand one truncated tool block"),
    SlashCommandMetadata("/export", "Show transcript export command"),
    SlashCommandMetadata("/editor", "Show external-editor composition guidance"),
    SlashCommandMetadata("/readonly", "Switch permission mode to readonly"),
    SlashCommandMetadata("/help", "Show this help", ("/",)),
    SlashCommandMetadata("/exit", "Exit the interactive CLI", ("/quit",)),
)

_BUSY_SAFE_SLASH_COMMANDS = frozenset(
    {
        "/",
        "/context",
        "/context-review",
        "/copy",
        "/cost",
        "/details",
        "/editor",
        "/export",
        "/graph",
        "/help",
        "/mcp",
        "/memory",
        "/normal",
        "/overview",
        "/quiet",
        "/skills",
        "/status",
        "/tasks",
        "/telemetry",
        "/tokens",
        "/trace",
        "/verbose",
    }
)
_BUSY_SAFE_BARE_SLASH_COMMANDS = frozenset(
    {
        "/agents",
        "/browser",
        "/effort",
        "/model",
        "/permissions",
        "/statusline",
        "/tools",
    }
)


def slash_command_runs_while_busy(text: str) -> bool:
    parts = str(text or "").strip().split(maxsplit=1)
    if not parts:
        return False
    command = parts[0]
    return command in _BUSY_SAFE_SLASH_COMMANDS or (
        command in _BUSY_SAFE_BARE_SLASH_COMMANDS and len(parts) == 1
    )


def terminal_slash_commands() -> tuple[str, ...]:
    names: list[str] = []
    for command in SLASH_COMMANDS:
        names.append(command.name)
        for alias in command.aliases:
            if command.name == "/exit" and alias == "/quit":
                names.append(alias)
    return tuple(dict.fromkeys(names))


def slash_help_rows() -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for command in SLASH_COMMANDS:
        aliases = ", ".join(command.aliases)
        suffix = f" (also {aliases})" if aliases else ""
        rows.append((command.name, f"{command.description}{suffix}"))
    return tuple(rows)


def unknown_slash_command_message(
    command: str,
    *,
    available_commands: Iterable[str],
) -> str:
    normalized = str(command or "").strip().split(maxsplit=1)[0]
    candidates = tuple(dict.fromkeys(str(item).strip() for item in available_commands))
    matches = get_close_matches(normalized, candidates, n=1, cutoff=0.75)
    lines = [f"Unknown command: {normalized}"]
    if matches:
        lines.append(f"Did you mean {matches[0]}?")
    lines.append("Type / to view available commands.")
    return "\n".join(lines)


__all__ = [
    "SLASH_COMMANDS",
    "SlashCommandMetadata",
    "slash_command_runs_while_busy",
    "slash_help_rows",
    "terminal_slash_commands",
    "unknown_slash_command_message",
]
