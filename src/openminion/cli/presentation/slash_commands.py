from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommandMetadata:
    name: str
    description: str
    terminal_handler: str | None = None
    rich_handler: str | None = None
    aliases: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


SLASH_COMMANDS: tuple[SlashCommandMetadata, ...] = (
    SlashCommandMetadata(
        "/init", "Create OPENMINION.md for this project", "_slash_init", "_slash_init"
    ),
    SlashCommandMetadata(
        "/new", "Start a new session", "_slash_new", "_slash_new", ("/new session",)
    ),
    SlashCommandMetadata(
        "/clear", "Clear chat history", "_slash_clear", "_slash_clear", ("/cls",)
    ),
    SlashCommandMetadata(
        "/sessions",
        "Pick a session",
        "_slash_sessions",
        "_slash_sessions",
        ("/session",),
    ),
    SlashCommandMetadata(
        "/resume",
        "Resume a prior session with messages",
        "_slash_resume",
        "_slash_resume",
    ),
    SlashCommandMetadata(
        "/agents",
        "List agents or inspect/switch one",
        "_slash_agents",
        "_slash_agent",
        ("/agent",),
    ),
    SlashCommandMetadata(
        "/model",
        "Show or switch the active provider/model",
        "_slash_model",
        "_slash_model",
    ),
    SlashCommandMetadata(
        "/theme",
        "Show, switch, save, or reset the active theme",
        "_slash_theme",
        "_slash_theme",
    ),
    SlashCommandMetadata(
        "/tools", "Show available tools", "_slash_tools", "_slash_tools", ("/tool",)
    ),
    SlashCommandMetadata(
        "/mcp", "Show configured MCP servers and tools", "_slash_mcp", "_slash_mcp"
    ),
    SlashCommandMetadata(
        "/cost", "Show current session token / cost usage", "_slash_cost", "_slash_cost"
    ),
    SlashCommandMetadata(
        "/context", "Show visual context usage", "_slash_context", "_slash_context"
    ),
    SlashCommandMetadata(
        "/effort", "Show or set per-turn effort", "_slash_effort", "_slash_effort"
    ),
    SlashCommandMetadata(
        "/memory", "Show memory inventory", "_slash_memory", "_slash_memory"
    ),
    SlashCommandMetadata(
        "/skills", "Show skill inventory", "_slash_skills", "_slash_skills"
    ),
    SlashCommandMetadata(
        "/statusline",
        "Show or set status line preset/custom command",
        "_slash_statusline",
        "_slash_statusline",
    ),
    SlashCommandMetadata(
        "/undo", "Rewind latest turn or restore a file", "_slash_undo", "_slash_undo"
    ),
    SlashCommandMetadata(
        "/permissions",
        "Show or set sandbox approval mode",
        "_slash_permissions",
        "_slash_permissions",
    ),
    SlashCommandMetadata(
        "/diff", "Show workspace git diff", "_slash_diff", "_slash_diff"
    ),
    SlashCommandMetadata(
        "/compact",
        "Compact conversation history if supported",
        "_slash_compact",
        "_slash_compact",
    ),
    SlashCommandMetadata(
        "/queue",
        "Inspect or control queued type-ahead prompts",
        "_slash_queue",
        "_slash_queue",
    ),
    SlashCommandMetadata("/copy", "Copy last/selected message", None, "_slash_copy"),
    SlashCommandMetadata(
        "/status",
        "Show agent / model / session / dir",
        "_slash_status",
        "_slash_status",
    ),
    SlashCommandMetadata("/debug", "Toggle debug pane", None, "_slash_debug"),
    SlashCommandMetadata(
        "/quiet", "Hide tool blocks for the session", "_slash_quiet", "_slash_quiet"
    ),
    SlashCommandMetadata(
        "/normal", "Truncated tool blocks (default)", "_slash_normal", "_slash_normal"
    ),
    SlashCommandMetadata(
        "/verbose", "Show full tool block output", "_slash_verbose", "_slash_verbose"
    ),
    SlashCommandMetadata(
        "/details",
        "Toggle detailed tool blocks for the session",
        "_slash_details",
        "_slash_details",
    ),
    SlashCommandMetadata(
        "/expand", "Expand one truncated tool block", "_slash_expand", None
    ),
    SlashCommandMetadata(
        "/export", "Show transcript export command", "_slash_export", "_slash_export"
    ),
    SlashCommandMetadata(
        "/editor",
        "Show external-editor composition guidance",
        "_slash_editor",
        "_slash_editor",
    ),
    SlashCommandMetadata(
        "/readonly", "Switch permission mode to readonly", "_slash_readonly", None
    ),
    SlashCommandMetadata(
        "/dashboard",
        "Open the monitoring dashboard for chats, sessions, agents, and tools",
        "_slash_dashboard",
        "_slash_dashboard",
    ),
    SlashCommandMetadata(
        "/help", "Show this help", "_slash_help", "_slash_help", ("/",)
    ),
    SlashCommandMetadata(
        "/exit", "Exit the focus shell", "_slash_exit", "_slash_exit", ("/quit",)
    ),
)


def terminal_slash_commands() -> tuple[str, ...]:
    names: list[str] = []
    for command in SLASH_COMMANDS:
        if command.terminal_handler is None:
            continue
        names.append(command.name)
        for alias in command.aliases:
            if command.name == "/exit" and alias == "/quit":
                names.append(alias)
    return tuple(dict.fromkeys(names))


def rich_slash_command_registry() -> list[tuple[tuple[str, ...], str, str]]:
    rows: list[tuple[tuple[str, ...], str, str]] = []
    for command in SLASH_COMMANDS:
        if command.rich_handler is None:
            continue
        rows.append((command.names, command.description, command.rich_handler))
    return rows


def slash_help_rows(*, terminal_only: bool = False) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for command in SLASH_COMMANDS:
        if terminal_only and command.terminal_handler is None:
            continue
        aliases = ", ".join(command.aliases)
        suffix = f" (also {aliases})" if aliases else ""
        rows.append((command.name, f"{command.description}{suffix}"))
    return tuple(rows)


__all__ = [
    "SLASH_COMMANDS",
    "SlashCommandMetadata",
    "rich_slash_command_registry",
    "slash_help_rows",
    "terminal_slash_commands",
]
