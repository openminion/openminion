from __future__ import annotations

from typing import Any

from openminion.cli.presentation.styles import StyleToken, style_token
from openminion.cli.interactive.terminal.spinner import format_elapsed_label


_SEGMENT_SEP = "  ·  "
_SEGMENT_ATTRS = {
    "agent": "agent_label",
    "branch": "branch_label",
    "cost": "cost_label",
    "cwd": "cwd_label",
    "custom": "custom_label",
    "custom_label": "custom_label",
    "model": "model_label",
    "permission": "permission_mode",
    "permission_mode": "permission_mode",
    "statusline": "custom_label",
    "turn_status": "turn_status_label",
    "tokens": "tokens_label",
    "tool_name": "tool_name",
}


def _wrap(token: StyleToken, text: str, *, bold: bool = False) -> str:
    if not text:
        return ""
    open_code, close_code = style_token(token)
    if not open_code:
        return text
    if bold:
        return f"\033[1m{open_code}{text}{close_code}\033[22m"
    return f"{open_code}{text}{close_code}"


def _labeled_segment(label: str, value: str, token: StyleToken) -> str:
    return _wrap(StyleToken.MUTED, label) + _wrap(token, value)


def _active_labeled_segment(label: str, value: str) -> str:
    return _wrap(StyleToken.WARNING, label, bold=True) + _wrap(
        StyleToken.WARNING, value, bold=True
    )


def _token_severity(severity: str) -> StyleToken:
    if severity == "warning":
        return StyleToken.WARNING
    if severity == "error":
        return StyleToken.ERROR
    return StyleToken.SYSTEM


def _join_rows(*rows: str) -> str:
    return "\n".join(row for row in rows if row)


class TerminalStatusLine:
    def __init__(self) -> None:
        self.state: str = "idle"
        self.elapsed_seconds: float = 0.0
        self.tool_name: str = ""
        self.usage_summary: str = ""
        self.model_label: str = ""
        self.cwd_label: str = ""
        self.branch_label: str = ""
        self.tokens_label: str = ""
        self.cost_label: str = ""
        self.agent_label: str = ""
        self.permission_mode: str = "default"
        self.custom_label: str = ""
        self.turn_status_label: str = ""
        self.tokens_severity: str = "normal"
        self.queued_count: int = 0

    def set_state(self, **segments: Any) -> None:
        for key, value in segments.items():
            if value is None:
                continue
            if key == "elapsed_seconds":
                self.elapsed_seconds = float(value)
                continue
            if key == "queued_count":
                try:
                    self.queued_count = max(0, int(value))
                except (TypeError, ValueError):
                    self.queued_count = 0
                continue
            attr_name = _SEGMENT_ATTRS.get(key, key)
            if hasattr(self, attr_name):
                setattr(self, attr_name, str(value).strip() if value else "")

    def _stable_segments(self) -> list[str]:
        segments: list[str] = []
        if self.agent_label:
            segments.append(_wrap(StyleToken.USER, f"◆ {self.agent_label}"))
        if self.model_label:
            segments.append(
                _labeled_segment("model: ", self.model_label, StyleToken.SYSTEM)
            )
        if self.cwd_label:
            segments.append(_wrap(StyleToken.MUTED, f"cwd: {self.cwd_label}"))
        if self.branch_label:
            segments.append(_wrap(StyleToken.MUTED, f"git: {self.branch_label}"))
        if self.tokens_label:
            segments.append(
                _labeled_segment(
                    "tokens: ",
                    self.tokens_label,
                    _token_severity((self.tokens_severity or "normal").lower()),
                )
            )
        if self.cost_label:
            segments.append(_wrap(StyleToken.MUTED, f"cost: {self.cost_label}"))
        if self.permission_mode and self.permission_mode != "default":
            mode_kind = (
                StyleToken.WARNING
                if self.permission_mode == "readonly"
                else StyleToken.ERROR
            )
            segments.append(
                _labeled_segment("permissions: ", self.permission_mode, mode_kind)
            )
        return segments

    def _active_status_row(self) -> str:
        if not self.turn_status_label or self.state == "idle":
            return ""
        status = _wrap(StyleToken.WARNING, "● ", bold=True) + _active_labeled_segment(
            "brain: ", self.turn_status_label
        )
        elapsed = _wrap(
            StyleToken.WARNING,
            f"{_SEGMENT_SEP}{format_elapsed_label(self.elapsed_seconds)}",
            bold=True,
        )
        return f"{status}{elapsed}"

    def _stable_row(self) -> str:
        segments = self._stable_segments()
        if self.queued_count:
            segments.append(_wrap(StyleToken.WARNING, f"queued: {self.queued_count}"))
        if self.custom_label and self.state == "idle":
            segments.append(
                _labeled_segment("status: ", self.custom_label, StyleToken.SYSTEM)
            )
        sep = _wrap(StyleToken.MUTED, _SEGMENT_SEP)
        return sep.join(segments)

    def bottom_toolbar(self) -> str:
        active_row = self._active_status_row()
        stable_row = self._stable_row()
        if self.usage_summary:
            usage_styled = _wrap(StyleToken.SYSTEM, self.usage_summary)
            if stable_row:
                stable_row = f"{stable_row}   {usage_styled}"
            else:
                stable_row = usage_styled
        return _join_rows(active_row, stable_row)

    def live_turn_footer(self) -> str:
        sep = _wrap(StyleToken.MUTED, _SEGMENT_SEP)
        stable_row = sep.join(segment for segment in self._stable_segments() if segment)
        return _join_rows(self._active_status_row(), stable_row)
