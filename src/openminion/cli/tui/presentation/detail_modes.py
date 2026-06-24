from __future__ import annotations

from typing import Literal

ToolTranscriptDetailMode = Literal["quiet", "normal", "verbose"]


def resolve_details_mode(
    current: str,
    arg: str = "",
) -> tuple[ToolTranscriptDetailMode, str]:
    value = str(arg or "").strip().lower()
    normalized_current: ToolTranscriptDetailMode = (
        current if current in ("quiet", "normal", "verbose") else "normal"
    )
    if value in ("", "toggle"):
        if normalized_current == "verbose":
            return "normal", "details off; tool blocks return to concise mode"
        return "verbose", "details on; tool blocks show full output"
    if value in ("on", "verbose", "full", "details"):
        return "verbose", "details on; tool blocks show full output"
    if value in ("off", "normal", "concise"):
        return "normal", "details off; tool blocks return to concise mode"
    if value == "quiet":
        return "quiet", "details quiet; tool blocks hidden until /normal or /details on"
    return normalized_current, "usage: /details [on|off|quiet]"


__all__ = ["ToolTranscriptDetailMode", "resolve_details_mode"]
