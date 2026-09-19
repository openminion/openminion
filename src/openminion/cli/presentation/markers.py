from __future__ import annotations

from rich.text import Text

from openminion.cli.presentation.styles import (
    StyleToken,
    active_theme_color,
    is_color_enabled,
    style_token,
)

Marker = tuple[str, StyleToken]

MARKER_ASSISTANT: Marker = ("●", StyleToken.ASSISTANT)
MARKER_TOOL_RUNNING: Marker = ("●", StyleToken.WARNING)
MARKER_TOOL_OK: Marker = ("●", StyleToken.SUCCESS)
MARKER_TOOL_FAIL: Marker = ("●", StyleToken.ERROR)
MARKER_USER: Marker = ("◆", StyleToken.USER)
MARKER_FAIL_SUFFIX: Marker = ("✗", StyleToken.ERROR)


def marker_text(marker: Marker, *, bold: bool = False) -> Text:
    glyph, token = marker
    return Text(glyph, style=token_rich_style(token, bold=bold))


def marker_ansi(marker: Marker) -> str:
    glyph, token = marker
    if not is_color_enabled():
        return glyph
    open_code, close_code = style_token(token)
    if not open_code:
        return glyph
    return f"{open_code}{glyph}{close_code}"


def token_rich_style(
    token: StyleToken, *, bold: bool = False, dim: bool = False
) -> str:
    if not is_color_enabled():
        mods = [m for m in ("bold" if bold else "", "dim" if dim else "") if m]
        return " ".join(mods)
    parts: list[str] = []
    if bold:
        parts.append("bold")
    if dim:
        parts.append("dim")
    parts.append(active_theme_color(token))
    return " ".join(parts)


__all__ = [
    "MARKER_ASSISTANT",
    "MARKER_TOOL_RUNNING",
    "MARKER_TOOL_OK",
    "MARKER_TOOL_FAIL",
    "MARKER_USER",
    "MARKER_FAIL_SUFFIX",
    "Marker",
    "marker_ansi",
    "marker_text",
    "token_rich_style",
]
