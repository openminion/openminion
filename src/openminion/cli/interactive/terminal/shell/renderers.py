from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.text import Text

from openminion.api.core.profiles import AgentConfigActivationError
from openminion.cli.status import format_token_usage_summary
from openminion.cli.presentation.header import (
    format_api_adapter,
    format_connection_name,
    format_runtime_adapter,
    format_runtime_provider,
)
from openminion.cli.presentation.styles import StyleToken
from openminion.cli.presentation.markers import token_rich_style
from .labels import _runtime_label


def _render_sessions_list(*, runtime: Any, console: Console) -> None:
    """Print past sessions as a Rich table."""
    from rich.table import Table

    lister = getattr(runtime, "list_sessions", None)
    if not callable(lister):
        console.print(
            Text(
                "(/sessions: runtime does not expose list_sessions)",
                style=token_rich_style(StyleToken.MUTED),
            )
        )
        return
    try:
        items = lister()
    except Exception as exc:
        console.print(
            Text(
                f"(/sessions: error — {exc})",
                style=token_rich_style(StyleToken.ERROR),
            )
        )
        return
    if not items:
        console.print(
            Text("(no sessions)", style=token_rich_style(StyleToken.MUTED, italic=True))
        )
        return
    table = Table(show_header=True, header_style="bold", expand=False)
    table.add_column("")  # active marker
    table.add_column("Session", style=token_rich_style(StyleToken.INFO))
    table.add_column("Label")
    muted_style = token_rich_style(StyleToken.MUTED)
    table.add_column("Updated", style=muted_style)
    table.add_column("Channel", style=muted_style)
    table.add_column("Room", style=muted_style)
    for item in items:
        active = bool(getattr(item, "active", False))
        marker = "◆" if active else " "
        sid = str(getattr(item, "id", "") or "")
        label = str(getattr(item, "label", "") or sid[:12] or "—")
        meta = getattr(item, "meta", None)
        updated = channel = room = ""
        if isinstance(meta, dict):
            updated = str(meta.get("updated_at", "") or "")[:19]
            channel = str(meta.get("channel", "") or "")
            if str(meta.get("session_type", "") or "") == "room":
                routing = str(meta.get("room_routing_mode", "") or "addressed")
                count = int(meta.get("participant_count", 0) or 0)
                room = f"{routing}, {count} participants"
        table.add_row(
            Text(
                marker,
                style=token_rich_style(StyleToken.INFO, bold=True) if active else "",
            ),
            sid,
            label,
            updated,
            channel,
            room,
        )
    console.print(table)


def _render_status_block(*, runtime: Any, console: Console, working_dir: str) -> None:
    """Render the current agent, model, session, cwd, and usage snapshot."""
    agent = str(getattr(runtime, "agent_id", "") or "—")
    model = _runtime_label(runtime)
    provider = format_runtime_provider(runtime)
    adapter = format_runtime_adapter(runtime)
    session_id = str(getattr(runtime, "session_id", "") or "—")
    usage_summary = ""
    snapshot_getter = getattr(runtime, "token_usage_snapshot", None)
    if callable(snapshot_getter):
        try:
            usage_summary = format_token_usage_summary(snapshot_getter())
        except (AttributeError, TypeError, ValueError):
            usage_summary = ""
    console.print(Text("Status:", style="bold"))
    console.print(Text(f"  agent: {agent}"))
    console.print(Text(f"  provider: {provider}"))
    console.print(Text(f"  model: {model}"))
    if adapter:
        console.print(Text(f"  API adapter: {adapter}"))
    console.print(
        Text(f"  session: {session_id}", style=token_rich_style(StyleToken.MUTED))
    )
    console.print(
        Text(f"  cwd: {working_dir}", style=token_rich_style(StyleToken.MUTED))
    )
    console.print(
        Text(
            f"  permissions: {getattr(runtime, 'permission_mode', 'default')}",
            style=token_rich_style(StyleToken.MUTED),
        )
    )
    console.print(
        Text(
            "  added directories: "
            f"{int(getattr(runtime, 'added_workspace_root_count', 0) or 0)}",
            style=token_rich_style(StyleToken.MUTED),
        )
    )
    room_detector = getattr(runtime, "is_room_session", None)
    room_reporter = getattr(runtime, "room_participants_report", None)
    if callable(room_detector) and room_detector() and callable(room_reporter):
        console.print()
        console.print(Text(room_reporter()))
    if usage_summary:
        console.print(
            Text(
                f"  usage: {usage_summary}",
                style=token_rich_style(StyleToken.MUTED),
            )
        )
    else:
        console.print(
            Text(
                "  usage: (no usage data yet)",
                style=token_rich_style(StyleToken.MUTED, italic=True),
            )
        )


def _render_tools_list(*, runtime: Any, console: Console) -> None:
    """List registered tools as a Rich table."""
    from rich.table import Table

    lister = getattr(runtime, "list_tools", None)
    if not callable(lister):
        console.print(
            Text(
                "(/tools: runtime does not expose list_tools)",
                style=token_rich_style(StyleToken.MUTED),
            )
        )
        return
    try:
        pairs = lister()
    except Exception as exc:
        console.print(
            Text(
                f"(/tools: error — {exc})",
                style=token_rich_style(StyleToken.ERROR),
            )
        )
        return
    if not pairs:
        console.print(
            Text(
                "(no tools registered)",
                style=token_rich_style(StyleToken.MUTED, italic=True),
            )
        )
        return
    table = Table(show_header=True, header_style="bold", expand=False)
    table.add_column("Tool", style=token_rich_style(StyleToken.INFO))
    table.add_column("Status")
    for name, enabled in pairs:
        status_text = Text("enabled" if enabled else "disabled")
        if not enabled:
            status_text.stylize("dim")
        table.add_row(
            Text(str(name), style="" if enabled else "dim"),
            status_text,
        )
    console.print(table)


def _render_model_status(*, runtime: Any, console: Console) -> None:
    """Show the active agent's configured model connections."""
    from rich.table import Table

    lister = getattr(runtime, "list_models", None)
    if not callable(lister):
        console.print(
            Text(
                "(/model: runtime does not expose list_models)",
                style=token_rich_style(StyleToken.MUTED),
            )
        )
        return
    try:
        rows = lister()
    except Exception as exc:
        console.print(
            Text(
                f"(/model: error — {exc})",
                style=token_rich_style(StyleToken.ERROR),
            )
        )
        return
    model_name = _runtime_label(runtime)
    provider = format_connection_name(format_runtime_provider(runtime))
    adapter = format_runtime_adapter(runtime)
    agent = str(getattr(runtime, "agent_id", "") or "").strip() or "—"
    console.print(Text("Model selection", style="bold"))
    console.print(Text(f"agent: {agent}", style=token_rich_style(StyleToken.MUTED)))
    console.print(Text(f"current model: {model_name}", style="bold"))
    connection = f"connection: {provider}"
    if adapter:
        connection += f" · API format: {adapter}"
    console.print(Text(connection, style=token_rich_style(StyleToken.MUTED)))
    if not rows:
        console.print(
            Text(
                "(this agent has no configured models)",
                style=token_rich_style(StyleToken.MUTED, italic=True),
            )
        )
        return
    table = Table(show_header=True, header_style="bold", expand=False)
    table.add_column("")
    table.add_column("#", justify="right")
    table.add_column("Connection", style=token_rich_style(StyleToken.INFO))
    table.add_column("Model")
    table.add_column("API format")
    table.add_column("Default")
    for row in rows:
        marker = "◆" if row.active else " "
        table.add_row(
            Text(
                marker,
                style=token_rich_style(StyleToken.INFO, bold=True)
                if row.active
                else "",
            ),
            str(row.index),
            format_connection_name(row.connection_name),
            row.model,
            format_api_adapter(row.transport_adapter),
            "agent" if row.agent_default else "",
        )
    console.print(table)
    console.print(Text("Actions", style="bold"))
    for command, description in (
        ("/model use <#>", "use for this session; restored on resume"),
        ("/model default <#>", "save as this agent's default"),
        ("/model add <model>", "add to this connection and use now"),
        ("/model setup", "configure another connection and use now"),
    ):
        console.print(
            Text.assemble(
                (f"  {command:<20}", token_rich_style(StyleToken.SYSTEM)),
                (description, token_rich_style(StyleToken.MUTED)),
            )
        )


def _render_model_command(arg: str, *, runtime: Any, console: Console) -> None:
    arg = arg.strip()
    if not arg:
        _render_model_status(runtime=runtime, console=console)
        return
    action, _, target = arg.partition(" ")
    if action == "add":
        if not target.strip():
            console.print(
                Text(
                    "(/model: use `/model add <model-id>`)",
                    style=token_rich_style(StyleToken.ERROR),
                )
            )
            return
        try:
            selected = runtime.add_model(target.strip())
        except (AgentConfigActivationError, OSError, ValueError) as exc:
            console.print(
                Text(
                    f"(/model: {exc})",
                    style=token_rich_style(StyleToken.ERROR),
                )
            )
            return
        console.print(
            Text(
                f"(model: added {selected.model} to {selected.connection_name}; "
                "selected for this session; agent default unchanged)",
                style=token_rich_style(StyleToken.MUTED, italic=True),
            )
        )
        return
    if action == "default" and not target.strip():
        console.print(
            Text(
                "(/model: use `/model default <#>`)",
                style=token_rich_style(StyleToken.ERROR),
            )
        )
        return
    try:
        selected = (
            runtime.set_default_model(target.strip())
            if action == "default"
            else runtime.switch_model(target.strip() if action == "use" else arg)
        )
    except (AgentConfigActivationError, OSError, ValueError) as exc:
        console.print(
            Text(f"(/model: {exc})", style=token_rich_style(StyleToken.ERROR))
        )
        return
    suffix = (
        f"is now the default for agent {runtime.agent_id}"
        if action == "default"
        else "saved for this session"
    )
    console.print(
        Text(
            f"(model: {selected.connection_name} / {selected.model} — {suffix})",
            style=token_rich_style(StyleToken.MUTED, italic=True),
        )
    )


def _render_mcp_status(*, runtime: Any, console: Console) -> None:
    reporter = getattr(runtime, "mcp_status_report", None)
    if not callable(reporter):
        console.print(
            Text(
                "(/mcp: runtime does not expose mcp_status_report)",
                style=token_rich_style(StyleToken.MUTED),
            )
        )
        return
    try:
        body = str(reporter() or "").strip()
    except Exception as exc:
        console.print(
            Text(
                f"(/mcp: error — {exc})",
                style=token_rich_style(StyleToken.ERROR),
            )
        )
        return
    console.print(
        Text(
            body or "(no MCP data available)",
            style=token_rich_style(StyleToken.MUTED),
        )
    )


def _switch_theme_variant(variant: str, *, console: Console) -> None:
    """Switch the contrast variant."""
    from openminion.cli.constants import CLI_THEME_VARIANTS
    from openminion.cli.presentation.styles import set_theme_variant

    key = variant.strip().lower()
    if key not in CLI_THEME_VARIANTS:
        valid = ", ".join(sorted(CLI_THEME_VARIANTS))
        console.print(
            Text(
                f"(/theme variant: unknown variant {variant!r}; valid: {valid})",
                style=token_rich_style(StyleToken.ERROR),
            )
        )
        return
    set_theme_variant(key)
    console.print(
        Text(
            f"(theme variant: switched to {key} — session-scoped)",
            style=token_rich_style(StyleToken.MUTED, italic=True),
        )
    )


def _render_cost_snapshot(*, runtime: Any, console: Console) -> None:
    """Render durable cost facts for the active session."""
    report_getter = getattr(runtime, "token_cost_report", None)
    if not callable(report_getter):
        console.print(
            Text(
                "(/cost: runtime does not expose token_cost_report)",
                style=token_rich_style(StyleToken.MUTED),
            )
        )
        return
    try:
        summary = str(report_getter()).strip()
    except Exception as exc:
        console.print(
            Text(
                f"(/cost: error — {exc})",
                style=token_rich_style(StyleToken.ERROR),
            )
        )
        return
    if not summary:
        console.print(
            Text(
                "(no usage data yet)",
                style=token_rich_style(StyleToken.MUTED, italic=True),
            )
        )
        return
    console.print(Text(summary, style="bold"))
