from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.text import Text

from openminion.cli.status import format_token_usage_summary
from openminion.cli.presentation.styles import StyleToken
from openminion.cli.tui.presentation.markers import token_rich_style
from .labels import _runtime_label

_ERR_STYLE = token_rich_style(StyleToken.ERROR)
_INFO_STYLE = token_rich_style(StyleToken.INFO)
_INFO_BOLD_STYLE = token_rich_style(StyleToken.INFO, bold=True)
_MUTED_STYLE = token_rich_style(StyleToken.MUTED)
_MUTED_ITALIC_STYLE = f"italic {_MUTED_STYLE}" if _MUTED_STYLE else "italic"
_SYSTEM_STYLE = token_rich_style(StyleToken.SYSTEM)


def _render_sessions_list(*, runtime: Any, console: Console) -> None:
    """Print past sessions as a Rich table."""
    from rich.table import Table

    lister = getattr(runtime, "list_sessions", None)
    if not callable(lister):
        console.print(
            Text(
                "(/sessions: runtime does not expose list_sessions)", style=_MUTED_STYLE
            )
        )
        return
    try:
        items = lister()
    except Exception as exc:
        console.print(Text(f"(/sessions: error — {exc})", style=_ERR_STYLE))
        return
    if not items:
        console.print(Text("(no sessions)", style=_MUTED_ITALIC_STYLE))
        return
    table = Table(show_header=True, header_style="bold", expand=False)
    table.add_column("")  # active marker
    table.add_column("Session", style=_INFO_STYLE)
    table.add_column("Label")
    table.add_column("Updated", style=_MUTED_STYLE)
    table.add_column("Channel", style=_MUTED_STYLE)
    for item in items:
        active = bool(getattr(item, "active", False))
        marker = "◆" if active else " "
        sid = str(getattr(item, "id", "") or "")
        label = str(getattr(item, "label", "") or sid[:12] or "—")
        meta = getattr(item, "meta", None) or {}
        updated = (
            str(meta.get("updated_at", "") or "")[:19] if isinstance(meta, dict) else ""
        )
        channel = str(meta.get("channel", "") or "") if isinstance(meta, dict) else ""
        table.add_row(
            Text(marker, style=_INFO_BOLD_STYLE if active else ""),
            sid,
            label,
            updated,
            channel,
        )
    console.print(table)


def _render_status_block(*, runtime: Any, console: Console, working_dir: str) -> None:
    """Render the current agent, model, session, cwd, and usage snapshot."""
    agent = str(getattr(runtime, "agent_id", "") or "—")
    model = _runtime_label(runtime)
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
    console.print(Text(f"  model: {model}"))
    console.print(Text(f"  session: {session_id}", style=_MUTED_STYLE))
    console.print(Text(f"  cwd: {working_dir}", style=_MUTED_STYLE))
    if usage_summary:
        console.print(Text(f"  usage: {usage_summary}", style=_MUTED_STYLE))
    else:
        console.print(Text("  usage: (no usage data yet)", style=_MUTED_ITALIC_STYLE))


def _render_tools_list(*, runtime: Any, console: Console) -> None:
    """List registered tools as a Rich table."""
    from rich.table import Table

    lister = getattr(runtime, "list_tools", None)
    if not callable(lister):
        console.print(
            Text("(/tools: runtime does not expose list_tools)", style=_MUTED_STYLE)
        )
        return
    try:
        pairs = lister()
    except Exception as exc:
        console.print(Text(f"(/tools: error — {exc})", style=_ERR_STYLE))
        return
    if not pairs:
        console.print(Text("(no tools registered)", style=_MUTED_ITALIC_STYLE))
        return
    table = Table(show_header=True, header_style="bold", expand=False)
    table.add_column("Tool", style=_INFO_STYLE)
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
    """Show the current model and configured providers."""
    from rich.table import Table

    lister = getattr(runtime, "list_models", None)
    if not callable(lister):
        console.print(
            Text("(/model: runtime does not expose list_models)", style=_MUTED_STYLE)
        )
        return
    try:
        rows = lister()
    except Exception as exc:
        console.print(Text(f"(/model: error — {exc})", style=_ERR_STYLE))
        return
    provider_name = str(getattr(runtime, "provider_name", "") or "")
    model_name = str(getattr(runtime, "model_name", "") or "")
    header = (
        f"current: {provider_name}/{model_name}"
        if model_name
        else (f"current: {provider_name or '(unset)'}")
    )
    console.print(Text(header, style="bold"))
    if not rows:
        console.print(Text("(no providers configured)", style=_MUTED_ITALIC_STYLE))
        return
    table = Table(show_header=True, header_style="bold", expand=False)
    table.add_column("")
    table.add_column("Provider", style=_INFO_STYLE)
    table.add_column("Configured model")
    for name, configured_model, is_active in rows:
        marker = "◆" if is_active else " "
        table.add_row(
            Text(marker, style=_INFO_BOLD_STYLE if is_active else ""),
            name,
            configured_model or "(none)",
        )
    console.print(table)
    console.print(
        Text(
            "Switch with `/model <provider>` or `/model <provider>/<model>`. "
            "Session-scoped; restart reverts.",
            style=_MUTED_ITALIC_STYLE,
        )
    )


def _render_mcp_status(*, runtime: Any, console: Console) -> None:
    reporter = getattr(runtime, "mcp_status_report", None)
    if not callable(reporter):
        console.print(
            Text(
                "(/mcp: runtime does not expose mcp_status_report)", style=_MUTED_STYLE
            )
        )
        return
    try:
        body = str(reporter() or "").strip()
    except Exception as exc:
        console.print(Text(f"(/mcp: error — {exc})", style=_ERR_STYLE))
        return
    console.print(Text(body or "(no MCP data available)", style=_MUTED_STYLE))


def _render_theme_status(*, console: Console) -> None:
    """Show the active theme, variant, and available themes."""
    from openminion.cli.theme import SHIPPED_THEMES
    from openminion.cli.presentation.styles import (
        get_active_theme_name,
        get_theme_variant,
    )

    active = get_active_theme_name()
    variant = get_theme_variant()
    body_lines = [
        Text.assemble(
            ("active:   ", _MUTED_STYLE),
            (f"{active}", _SYSTEM_STYLE),
        ),
        Text.assemble(
            ("variant:  ", _MUTED_STYLE),
            (f"{variant}", _SYSTEM_STYLE),
        ),
        Text.assemble(
            ("available:", _MUTED_STYLE),
            (f"  {', '.join(sorted(SHIPPED_THEMES.keys()))}", _SYSTEM_STYLE),
        ),
    ]
    for line in body_lines:
        console.print(line)
    console.print(
        Text(
            "Switch with `/theme <name>` or `/theme variant "
            "<balanced|high_contrast>`. Session-scoped; restart reverts.",
            style=_MUTED_ITALIC_STYLE,
        )
    )


def _switch_theme(name: str, *, console: Console) -> None:
    """Switch the active theme by name."""
    from openminion.cli.theme import SHIPPED_THEMES
    from openminion.cli.presentation.styles import set_active_theme

    key = name.strip().lower()
    if key not in SHIPPED_THEMES:
        valid = ", ".join(sorted(SHIPPED_THEMES.keys()))
        console.print(
            Text(
                f"(/theme: unknown theme {name!r}; valid options: {valid})",
                style=_ERR_STYLE,
            )
        )
        return
    set_active_theme(SHIPPED_THEMES[key])
    new_muted_italic = (
        f"italic {token_rich_style(StyleToken.MUTED)}"
        if token_rich_style(StyleToken.MUTED)
        else "italic"
    )
    console.print(
        Text(
            f"(theme: switched to {key} — session-scoped)",
            style=new_muted_italic,
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
                style=_ERR_STYLE,
            )
        )
        return
    set_theme_variant(key)
    console.print(
        Text(
            f"(theme variant: switched to {key} — session-scoped)",
            style=_MUTED_ITALIC_STYLE,
        )
    )


def _render_cost_snapshot(*, runtime: Any, console: Console) -> None:
    """Render a one-line snapshot of current token usage."""
    snapshot_getter = getattr(runtime, "token_usage_snapshot", None)
    if not callable(snapshot_getter):
        console.print(
            Text(
                "(/cost: runtime does not expose token_usage_snapshot)",
                style=_MUTED_STYLE,
            )
        )
        return
    try:
        snapshot = snapshot_getter()
        summary = format_token_usage_summary(snapshot)
    except Exception as exc:
        console.print(Text(f"(/cost: error — {exc})", style=_ERR_STYLE))
        return
    if not summary:
        console.print(Text("(no usage data yet)", style=_MUTED_ITALIC_STYLE))
        return
    console.print(Text(f"cost: {summary}", style="bold"))
