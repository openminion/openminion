from __future__ import annotations

from .models import Theme


def theme_variables_dict(theme: Theme) -> dict[str, str]:
    return {
        f"openminion-{name.replace('_', '-')}": getattr(theme, name)
        for name in theme.color_field_names()
    }


def as_tcss_preamble(theme: Theme) -> str:
    lines = [
        f"/* OpenMinion shared theme: {theme.name} */",
        f"$openminion-chat-user-bg: {theme.chat_user_bg};",
        f"$openminion-chat-user-fg: {theme.chat_user_fg};",
        f"$openminion-chat-agent-bg: {theme.chat_agent_bg};",
        f"$openminion-chat-agent-fg: {theme.chat_agent_fg};",
        f"$openminion-chat-system-bg: {theme.chat_system_bg};",
        f"$openminion-chat-system-fg: {theme.chat_system_fg};",
        f"$openminion-chat-tool-bg: {theme.chat_tool_bg};",
        f"$openminion-chat-tool-fg: {theme.chat_tool_fg};",
        f"$openminion-chat-error-bg: {theme.chat_error_bg};",
        f"$openminion-chat-error-fg: {theme.chat_error_fg};",
        f"$openminion-surface-app-bg: {theme.surface_app_bg};",
        f"$openminion-surface-panel-bg: {theme.surface_panel_bg};",
        f"$openminion-surface-divider: {theme.surface_divider};",
        f"$openminion-text-primary: {theme.text_primary};",
        f"$openminion-text-secondary: {theme.text_secondary};",
        f"$openminion-text-muted: {theme.text_muted};",
        f"$openminion-text-accent: {theme.text_accent};",
        f"$openminion-state-ok: {theme.state_ok};",
        f"$openminion-state-warning: {theme.state_warning};",
        f"$openminion-state-error: {theme.state_error};",
        f"$openminion-state-offline: {theme.state_offline};",
        f"$openminion-state-highlight: {theme.state_highlight};",
    ]
    return "\n".join(lines) + "\n"


__all__ = ["as_tcss_preamble", "theme_variables_dict"]
