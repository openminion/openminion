from __future__ import annotations

from typing import Any

from openminion.cli.presentation.styles import StyleToken, style_token


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


def _token_severity(severity: str) -> StyleToken:
    if severity == "warning":
        return StyleToken.WARNING
    if severity == "error":
        return StyleToken.ERROR
    return StyleToken.SYSTEM


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
        self.input_state: str = "empty"
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

    def bottom_toolbar(self) -> str:
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
        if self.queued_count:
            segments.append(_wrap(StyleToken.MUTED, f"queued: {self.queued_count}"))
        if self.custom_label and self.state == "idle":
            segments.append(
                _labeled_segment("status: ", self.custom_label, StyleToken.SYSTEM)
            )
        sep = _wrap(StyleToken.MUTED, _SEGMENT_SEP)
        prefix = sep.join(segments)
        if self.usage_summary:
            usage_styled = _wrap(StyleToken.SYSTEM, self.usage_summary)
            if prefix:
                return f"{prefix}   {usage_styled}"
            return usage_styled
        return prefix

    def live_turn_footer(self) -> str:
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
        if self.queued_count:
            segments.append(_wrap(StyleToken.MUTED, f"queued: {self.queued_count}"))
        sep = _wrap(StyleToken.MUTED, _SEGMENT_SEP)
        return sep.join(segment for segment in segments if segment)
