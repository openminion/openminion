from .models import Theme

LIGHT = Theme(
    name="light",
    chat_user_bg="#e8e8e8",
    chat_user_fg="#1a1a1a",
    chat_agent_bg="#f0f7ff",
    chat_agent_fg="#1a1a1a",
    chat_system_bg="#fff8e1",
    chat_system_fg="#3e2723",
    chat_tool_bg="#f5f5f5",
    chat_tool_fg="#263238",
    chat_error_bg="#ffebee",
    chat_error_fg="#8b0000",
    surface_app_bg="#fafafa",
    surface_panel_bg="#ffffff",
    surface_divider="#d0d0d0",
    text_primary="#1a1a1a",
    text_secondary="#404040",
    text_muted="#5e5e5e",
    text_accent="#0050a0",
    state_ok="#1b5e20",
    state_warning="#a55300",
    state_error="#a30c0c",
    state_offline="#5e5e5e",
    state_highlight="#0d47a1",
)


DARK = Theme(
    name="dark",
    chat_user_bg="#2d2d2d",
    chat_user_fg="#f0f0f0",
    chat_agent_bg="#1a2332",
    chat_agent_fg="#f0f0f0",
    chat_system_bg="#2b2620",
    chat_system_fg="#f5e6a8",
    chat_tool_bg="#252525",
    chat_tool_fg="#cfd8dc",
    chat_error_bg="#3a1f1f",
    chat_error_fg="#ffb0b0",
    surface_app_bg="#1e1e1e",
    surface_panel_bg="#2a2a2a",
    surface_divider="#3a3a3a",
    text_primary="#f0f0f0",
    text_secondary="#c0c0c0",
    text_muted="#9a9a9a",
    text_accent="#5c9eff",
    state_ok="#66bb6a",
    state_warning="#ffa726",
    state_error="#ff7878",
    state_offline="#9a9a9a",
    state_highlight="#5c9eff",
)


SHIPPED_THEMES: dict[str, Theme] = {
    LIGHT.name: LIGHT,
    DARK.name: DARK,
}


__all__ = ["DARK", "LIGHT", "SHIPPED_THEMES"]
