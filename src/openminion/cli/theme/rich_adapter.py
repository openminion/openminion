from __future__ import annotations

from rich.theme import Theme as RichTheme

from .models import Theme


def as_rich_theme(theme: Theme) -> RichTheme:
    return RichTheme(
        {
            "chat.user": f"{theme.chat_user_fg} on {theme.chat_user_bg}",
            "chat.agent": f"{theme.chat_agent_fg} on {theme.chat_agent_bg}",
            "chat.system": f"{theme.chat_system_fg} on {theme.chat_system_bg}",
            "chat.tool": f"{theme.chat_tool_fg} on {theme.chat_tool_bg}",
            "chat.error": f"{theme.chat_error_fg} on {theme.chat_error_bg}",
            "chat.user.fg": theme.chat_user_fg,
            "chat.agent.fg": theme.chat_agent_fg,
            "chat.system.fg": theme.chat_system_fg,
            "chat.tool.fg": theme.chat_tool_fg,
            "chat.error.fg": theme.chat_error_fg,
            "surface.app": f"on {theme.surface_app_bg}",
            "surface.panel": f"on {theme.surface_panel_bg}",
            "surface.divider": theme.surface_divider,
            "text.primary": theme.text_primary,
            "text.secondary": theme.text_secondary,
            "text.muted": theme.text_muted,
            "text.accent": theme.text_accent,
            "state.ok": theme.state_ok,
            "state.warning": theme.state_warning,
            "state.error": theme.state_error,
            "state.offline": theme.state_offline,
            "state.highlight": theme.state_highlight,
        }
    )


__all__ = ["as_rich_theme"]
