from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from textwrap import fill

from openminion.cli.presentation.custom_commands import CustomCommand


@dataclass(frozen=True)
class SlashCommandMetadata:
    name: str
    description: str
    usage: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    note: str = ""

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


SLASH_COMMANDS: tuple[SlashCommandMetadata, ...] = (
    SlashCommandMetadata("/init", "Create OPENMINION.md for this project", ("/init",)),
    SlashCommandMetadata(
        "/new",
        "Start a new session",
        ("/new", "/new session"),
        aliases=("/new session",),
    ),
    SlashCommandMetadata("/close", "Close the current session", ("/close",)),
    SlashCommandMetadata(
        "/clear", "Clear chat history", ("/clear", "/cls"), aliases=("/cls",)
    ),
    SlashCommandMetadata(
        "/sessions", "List sessions", ("/sessions", "/session"), aliases=("/session",)
    ),
    SlashCommandMetadata(
        "/participants", "Show room participants and routing", ("/participants",)
    ),
    SlashCommandMetadata(
        "/invite",
        "Invite a room agent or human",
        ("/invite agent <id>", "/invite human <id> [role]"),
    ),
    SlashCommandMetadata(
        "/kick", "Remove a room participant", ("/kick <agent|human> <id>",)
    ),
    SlashCommandMetadata(
        "/activate", "Set the active room agent", ("/activate <agent-id>",)
    ),
    SlashCommandMetadata(
        "/routing",
        "Show or set room routing",
        ("/routing", "/routing <addressed|broadcast|sequential>"),
    ),
    SlashCommandMetadata(
        "/resume", "Resume a prior session with messages", ("/resume",)
    ),
    SlashCommandMetadata(
        "/agents",
        "List configured agents or filter by exact ID or label",
        ("/agents", "/agents <agent-id-or-label>"),
        aliases=("/agent",),
        note="Agent selection: start Focus with --profile <agent-id> or --agent <agent-id>.",
    ),
    SlashCommandMetadata(
        "/delegate",
        "Delegate work or inspect a delegated task",
        (
            "/delegate [sync|async] <agent> <instruction...>",
            "/delegate status|result|resume|cancel <task-id>",
            "/delegate review '<review-request-json>'",
            "/delegate accept|reject '<child-artifact-json>'",
        ),
    ),
    SlashCommandMetadata(
        "/model",
        "Show or choose this agent's configured model",
        (
            "/model",
            "/model use <#|model>",
            "/model default <#>",
            "/model add <model-id>",
            "/model setup",
        ),
    ),
    SlashCommandMetadata(
        "/theme",
        "Show or switch the active theme",
        (
            "/theme",
            "/theme <name>",
            "/theme variant <balanced|high_contrast>",
        ),
    ),
    SlashCommandMetadata(
        "/tools",
        "Show or manage available tool profiles",
        (
            "/tools [status|list]",
            "/tools activate <profile> [key=value ...]",
            "/tools deactivate <profile> [target=<id>]",
        ),
        aliases=("/tool",),
    ),
    SlashCommandMetadata(
        "/browser",
        "Show browser status or control tabs",
        (
            "/browser [status]",
            "/browser tabs [provider=<id>]",
            "/browser navigate <url> [tab=<id>] [provider=<id>]",
            "/browser stop [sidecar=0 instance=<id>] [kill=1]",
        ),
    ),
    SlashCommandMetadata("/mcp", "Show configured MCP servers and tools", ("/mcp",)),
    SlashCommandMetadata(
        "/cost", "Show live token usage and available cost", ("/cost",)
    ),
    SlashCommandMetadata(
        "/tokens",
        "Show durable token usage details",
        ("/tokens", "/tokens recent [1..20]"),
    ),
    SlashCommandMetadata("/context", "Show visual context usage", ("/context",)),
    SlashCommandMetadata(
        "/context-review",
        "Review memory and context evidence",
        (
            "/context-review [session=<id>] [canary=<path>] [calibration=<path>] [artifacts=<dir>]",
        ),
    ),
    SlashCommandMetadata(
        "/overview", "Show the read-only operations overview", ("/overview",)
    ),
    SlashCommandMetadata(
        "/goal",
        "Create, bind, inspect, or run a session goal",
        (
            "/goal [list|all|status|inspect|evidence|pause|resume|stop|clear]",
            "/goal create <description> [--id <goal-id>]",
            "/goal bind|show|abort|verify <goal-id>",
            "/goal run <goal-id> [--replay outcome:reason,...]",
        ),
    ),
    SlashCommandMetadata(
        "/project",
        "Start durable coding or research work",
        (
            "/project",
            "/project start --goal <text> [--repository <path>] --verify-command <command>",
            "  [--verification-domain coding|research] [--max-iterations <n>]",
            "  [--max-wall-clock-ms <n>] [--max-tool-calls <n>]",
            "  [--expected-check <name>] [--release-tools]",
        ),
    ),
    SlashCommandMetadata(
        "/effort",
        "Show or set per-turn effort",
        ("/effort", "/effort <low|medium|high|xhigh|max|default>"),
    ),
    SlashCommandMetadata("/memory", "Show memory health and inventory", ("/memory",)),
    SlashCommandMetadata(
        "/graph",
        "Show commands to query, refresh, or view configured graphs",
        (
            "/graph [status|current|dry-run|json]",
            "/graph query <source> <text>",
            "/graph neighborhood <source> <entity>",
            "/graph refresh <source>",
            "/graph html [path]",
            "/graph third <provider>",
        ),
    ),
    SlashCommandMetadata(
        "/tasks",
        "Show or control task inventory",
        (
            "/tasks [task-id]",
            "/tasks <pause|resume|cancel> <task-id>",
        ),
        aliases=("/task",),
    ),
    SlashCommandMetadata(
        "/skills",
        "List skills or view one",
        ("/skills", "/skills <skill-id>"),
    ),
    SlashCommandMetadata(
        "/statusline",
        "Show or set the status line",
        (
            "/statusline",
            "/statusline <default|minimal|ops|cost>",
            "/statusline <custom-command>",
        ),
    ),
    SlashCommandMetadata(
        "/undo",
        "Rewind the latest turn or restore a file",
        ("/undo", "/undo file <path>"),
    ),
    SlashCommandMetadata(
        "/permissions",
        "Show or set the sandbox approval mode",
        (
            "/permissions",
            "/permissions <default|readonly|bypass|cycle>",
            "/permissions <tool> <ask|auto|bypass|readonly|default>",
        ),
    ),
    SlashCommandMetadata(
        "/diff", "Show workspace git diff", ("/diff [--staged] [path]",)
    ),
    SlashCommandMetadata(
        "/review",
        "Review the current or supplied diff",
        (
            "/review [--staged] [path]",
            "/review --file <workspace-diff>",
            "/review --diff <diff-text>",
        ),
    ),
    SlashCommandMetadata(
        "/compact", "Compact conversation history if supported", ("/compact",)
    ),
    SlashCommandMetadata(
        "/queue",
        "Inspect or control queued type-ahead prompts",
        ("/queue", "/queue clear|run-next", "/queue drop <index>"),
    ),
    SlashCommandMetadata("/copy", "Copy the latest message", ("/copy",)),
    SlashCommandMetadata(
        "/status", "Show agent, model, session, and directory", ("/status",)
    ),
    SlashCommandMetadata(
        "/telemetry",
        "Show local telemetry diagnostics",
        (
            "/telemetry [latest|failed]",
            "/telemetry invocation <invocation-id>",
            "/telemetry events [--limit <1..100>]",
        ),
    ),
    SlashCommandMetadata(
        "/trace",
        "List or show local trace metadata",
        ("/trace [list [--limit <1..100>]]", "/trace show <relative-path>"),
    ),
    SlashCommandMetadata("/quiet", "Hide tool blocks for the session", ("/quiet",)),
    SlashCommandMetadata(
        "/normal", "Show concise tool blocks for the session", ("/normal",)
    ),
    SlashCommandMetadata("/verbose", "Show full tool block output", ("/verbose",)),
    SlashCommandMetadata(
        "/details",
        "Toggle or set tool-block detail",
        ("/details [on|off|quiet|toggle]",),
    ),
    SlashCommandMetadata(
        "/expand", "Expand one truncated tool block", ("/expand [block-number]",)
    ),
    SlashCommandMetadata("/export", "Show the transcript export command", ("/export",)),
    SlashCommandMetadata(
        "/editor", "Show external-editor composition guidance", ("/editor",)
    ),
    SlashCommandMetadata(
        "/readonly",
        "Toggle or set read-only mode",
        ("/readonly [on|off|toggle]",),
    ),
    SlashCommandMetadata(
        "/help",
        "List commands or show one command's help",
        ("/help", "/help <command>", "/<command> --help", "/<command> ?"),
        aliases=("/",),
    ),
    SlashCommandMetadata(
        "/exit", "Exit the interactive CLI", ("/exit", "/quit"), aliases=("/quit",)
    ),
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
    if parse_slash_help_target(text) is not None:
        return True
    parts = str(text or "").strip().split(maxsplit=1)
    if not parts:
        return False
    command = canonical_slash_command_name(parts[0])
    if command == "/tasks" and len(parts) > 1:
        action = parts[1].split(maxsplit=1)[0].lower()
        if action in {"pause", "resume", "cancel"}:
            return False
    return command in _BUSY_SAFE_SLASH_COMMANDS or (
        command in _BUSY_SAFE_BARE_SLASH_COMMANDS and len(parts) == 1
    )


def terminal_slash_commands() -> tuple[str, ...]:
    names: list[str] = []
    for command in SLASH_COMMANDS:
        names.append(command.name)
        names.extend(command.aliases)
    return tuple(dict.fromkeys(names))


def canonical_slash_command_name(name: str) -> str:
    normalized = str(name or "").strip()
    for command in SLASH_COMMANDS:
        if normalized == command.name or normalized in command.aliases:
            return command.name
    return normalized


def slash_command_metadata(name: str) -> SlashCommandMetadata | None:
    normalized = str(name or "").strip()
    if normalized and not normalized.startswith("/"):
        normalized = f"/{normalized}"
    for command in SLASH_COMMANDS:
        if normalized in command.names:
            return command
    return None


def canonical_slash_command(text: str) -> str:
    parts = str(text or "").strip().split(maxsplit=1)
    if not parts:
        return ""
    command = canonical_slash_command_name(parts[0])
    return f"{command} {parts[1]}" if len(parts) > 1 else command


def slash_help_rows() -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for command in SLASH_COMMANDS:
        aliases = ", ".join(command.aliases)
        suffix = f" (also {aliases})" if aliases else ""
        rows.append((command.name, f"{command.description}{suffix}"))
    return tuple(rows)


def parse_slash_help_target(text: str) -> str | None:
    stripped = str(text or "").strip()
    if not stripped:
        return None
    if stripped in {"/", "/help"}:
        return ""
    parts = stripped.split(maxsplit=1)
    command = parts[0]
    operand = parts[1] if len(parts) == 2 else ""
    if command == "/help":
        if operand in {"--help", "?"}:
            return "/help"
        return operand
    if operand in {"--help", "?"}:
        return command
    return None


def format_slash_command_help(command: SlashCommandMetadata, *, width: int = 80) -> str:
    heading = fill(
        f"{command.name} — {command.description}",
        width=width,
        subsequent_indent="  ",
    )
    lines = [heading, "", "Usage:"]
    lines.extend(
        fill(usage, width=width, initial_indent="  ", subsequent_indent="    ")
        for usage in command.usage
    )
    if command.aliases:
        label = "Alias" if len(command.aliases) == 1 else "Aliases"
        lines.extend(("", f"{label}: {', '.join(command.aliases)}"))
    if command.note:
        lines.extend(("", fill(command.note, width=width, subsequent_indent="  ")))
    return "\n".join(lines)


def format_slash_help(
    target: str = "",
    *,
    custom_commands: Iterable[SlashCommandMetadata] = (),
    width: int = 80,
) -> str:
    built_in_names = frozenset(terminal_slash_commands())
    custom = tuple(
        sorted(
            (
                command
                for command in custom_commands
                if command.name not in built_in_names
            ),
            key=lambda command: command.name,
        )
    )
    normalized_target = str(target or "").strip()
    if not normalized_target:
        lines = ["Slash commands:"]
        rows = (*slash_help_rows(), *((item.name, item.description) for item in custom))
        for name, description in rows:
            prefix = f"  {name:<12} "
            lines.append(
                fill(
                    description,
                    width=width,
                    initial_indent=prefix,
                    subsequent_indent=" " * len(prefix),
                )
            )
        lines.extend(
            (
                "",
                "Use /help <command>, /<command> --help, or /<command> ? for details.",
            )
        )
        return "\n".join(lines)
    if len(normalized_target.split()) != 1:
        help_command = next(
            command for command in SLASH_COMMANDS if command.name == "/help"
        )
        return format_slash_command_help(help_command, width=width)
    command = slash_command_metadata(normalized_target)
    normalized_name = normalized_target
    if not normalized_name.startswith("/"):
        normalized_name = f"/{normalized_name}"
    if command is None:
        command = next((item for item in custom if item.name == normalized_name), None)
    if command is not None:
        return format_slash_command_help(command, width=width)
    return unknown_slash_command_message(
        normalized_name,
        available_commands=(
            *terminal_slash_commands(),
            *(item.name for item in custom),
        ),
    )


def custom_slash_metadata(
    custom_commands: Mapping[str, CustomCommand],
) -> tuple[SlashCommandMetadata, ...]:
    return tuple(
        SlashCommandMetadata(
            name=name,
            description=command.description or "custom command",
            usage=(command.usage or name,),
            note=f"Source: {command.source}",
        )
        for name, command in custom_commands.items()
    )


def contextual_slash_help(
    text: str,
    custom_commands: Mapping[str, CustomCommand],
    *,
    width: int = 80,
) -> str | None:
    target = parse_slash_help_target(text)
    if target is None:
        return None
    return format_slash_help(
        target,
        custom_commands=custom_slash_metadata(custom_commands),
        width=width,
    )


def slash_completion_catalog(
    custom_commands: Mapping[str, CustomCommand],
) -> dict[str, str]:
    primary_names = {command.name for command in SLASH_COMMANDS}
    all_names = set(terminal_slash_commands())
    catalog = {
        name: description
        for name, description in slash_help_rows()
        if name in primary_names
    }
    for command in custom_slash_metadata(custom_commands):
        if command.name not in all_names:
            catalog[command.name] = command.description
    return catalog


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
    "canonical_slash_command",
    "canonical_slash_command_name",
    "contextual_slash_help",
    "custom_slash_metadata",
    "format_slash_command_help",
    "format_slash_help",
    "parse_slash_help_target",
    "slash_completion_catalog",
    "slash_command_metadata",
    "slash_command_runs_while_busy",
    "slash_help_rows",
    "terminal_slash_commands",
    "unknown_slash_command_message",
]
