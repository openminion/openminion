from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Theme:
    name: str

    chat_user_bg: str
    chat_user_fg: str
    chat_agent_bg: str
    chat_agent_fg: str
    chat_system_bg: str
    chat_system_fg: str
    chat_tool_bg: str
    chat_tool_fg: str
    chat_error_bg: str
    chat_error_fg: str

    surface_app_bg: str
    surface_panel_bg: str
    surface_divider: str

    text_primary: str
    text_secondary: str
    text_muted: str
    text_accent: str

    state_ok: str
    state_warning: str
    state_error: str
    state_offline: str
    state_highlight: str

    def color_pairs(self) -> list[tuple[str, str, str]]:
        return [
            ("chat_user", self.chat_user_fg, self.chat_user_bg),
            ("chat_agent", self.chat_agent_fg, self.chat_agent_bg),
            ("chat_system", self.chat_system_fg, self.chat_system_bg),
            ("chat_tool", self.chat_tool_fg, self.chat_tool_bg),
            ("chat_error", self.chat_error_fg, self.chat_error_bg),
        ]

    def color_field_names(self) -> list[str]:
        return [f.name for f in fields(self) if f.name != "name"]


__all__ = ["Theme"]
