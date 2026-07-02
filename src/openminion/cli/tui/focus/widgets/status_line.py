from __future__ import annotations

from textual.app import ComposeResult
from textual.css.query import QueryError
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label

from openminion.cli.tui.presentation.permissions import format_permission_status_label

_IDLE_HINTS = "^P palette   ^K clear   ^F search   ^D debug   ^T tools   Esc exit"

_HINT_TYPING = "Enter to send   Shift+Enter for newline   Esc to clear"
_HINT_SELECTING = "Ctrl+Y to copy   Esc to deselect"
_INPUT_STATE_HINTS: dict[str, str] = {
    "empty": _IDLE_HINTS,
    "typing": _HINT_TYPING,
    "selecting": _HINT_SELECTING,
}

_SEGMENT_SEP = " | "

_CONTEXT_WARN_PCT = 80
_CONTEXT_DANGER_PCT = 95
_CONTEXT_WARN_GLYPH = "⚠"
_CONTEXT_DANGER_GLYPH = "⛔"

TOKENS_SEVERITY_NORMAL = "normal"
TOKENS_SEVERITY_WARN = "warn"
TOKENS_SEVERITY_DANGER = "danger"


def classify_context_severity(
    used: int | None,
    limit: int | None,
) -> str:
    """Return the FIU-07 severity for `used / limit` token usage."""
    if not limit:
        return TOKENS_SEVERITY_NORMAL
    try:
        used_int = int(used or 0)
        limit_int = int(limit)
    except (TypeError, ValueError):
        return TOKENS_SEVERITY_NORMAL
    if limit_int <= 0:
        return TOKENS_SEVERITY_NORMAL
    pct = (used_int * 100) / limit_int
    if pct > _CONTEXT_DANGER_PCT:
        return TOKENS_SEVERITY_DANGER
    if pct >= _CONTEXT_WARN_PCT:
        return TOKENS_SEVERITY_WARN
    return TOKENS_SEVERITY_NORMAL


class FocusStatusLine(Widget):
    """Dynamic status line that mirrors the focus shell's runtime state."""

    DEFAULT_CSS = "FocusStatusLine { height: 1; }"

    state: reactive[str] = reactive("idle")
    elapsed_seconds: reactive[float] = reactive(0.0)
    tool_name: reactive[str] = reactive("")
    usage_summary: reactive[str] = reactive("")
    model_label: reactive[str] = reactive("")
    cwd_label: reactive[str] = reactive("")
    branch_label: reactive[str] = reactive("")
    tokens_label: reactive[str] = reactive("")
    cost_label: reactive[str] = reactive("")
    permission_mode: reactive[str] = reactive("default")
    action_policy_mode: reactive[str] = reactive("")
    custom_label: reactive[str] = reactive("")
    goal_loop_label: reactive[str] = reactive("")
    agent_label: reactive[str] = reactive("")
    queued_count: reactive[int] = reactive(0)
    input_state: reactive[str] = reactive("empty")
    tokens_severity: reactive[str] = reactive(TOKENS_SEVERITY_NORMAL)

    def compose(self) -> ComposeResult:
        yield Label(_IDLE_HINTS, id="focus-status-hints")

    def set_state(
        self,
        *,
        state: str | None = None,
        elapsed_seconds: float | None = None,
        tool_name: str | None = None,
        usage_summary: str | None = None,
        model: str | None = None,
        cwd: str | None = None,
        branch: str | None = None,
        tokens: str | None = None,
        cost: str | None = None,
        permission_mode: str | None = None,
        action_policy_mode: str | None = None,
        custom: str | None = None,
        goal_loop: str | None = None,
        agent: str | None = None,
        queued_count: int | None = None,
        input_state: str | None = None,
        tokens_severity: str | None = None,
    ) -> None:
        """Push new state from `FocusScreen`; reactives trigger a re-render."""
        if state is not None:
            self.state = str(state or "idle").strip().lower() or "idle"
        if elapsed_seconds is not None:
            self.elapsed_seconds = float(elapsed_seconds)
        if tool_name is not None:
            self.tool_name = str(tool_name or "").strip()
        if usage_summary is not None:
            self.usage_summary = str(usage_summary or "").strip()
        if model is not None:
            self.model_label = str(model or "").strip()
        if cwd is not None:
            self.cwd_label = str(cwd or "").strip()
        if branch is not None:
            self.branch_label = str(branch or "").strip()
        if tokens is not None:
            self.tokens_label = str(tokens or "").strip()
        if cost is not None:
            self.cost_label = str(cost or "").strip()
        if permission_mode is not None:
            self.permission_mode = (
                str(permission_mode or "default").strip().lower() or "default"
            )
        if action_policy_mode is not None:
            self.action_policy_mode = str(action_policy_mode or "").strip().lower()
        if custom is not None:
            self.custom_label = str(custom or "").strip()
        if goal_loop is not None:
            self.goal_loop_label = str(goal_loop or "").strip()
        if agent is not None:
            self.agent_label = str(agent or "").strip()
        if queued_count is not None:
            try:
                self.queued_count = max(0, int(queued_count))
            except (TypeError, ValueError):
                self.queued_count = 0
        if input_state is not None:
            normalized = str(input_state or "empty").strip().lower() or "empty"
            if normalized not in _INPUT_STATE_HINTS:
                normalized = "empty"
            self.input_state = normalized
        if tokens_severity is not None:
            sev = str(tokens_severity or "").strip().lower()
            if sev not in {
                TOKENS_SEVERITY_NORMAL,
                TOKENS_SEVERITY_WARN,
                TOKENS_SEVERITY_DANGER,
            }:
                sev = TOKENS_SEVERITY_NORMAL
            self.tokens_severity = sev

    def watch_state(self, _value: str) -> None:
        self._refresh()

    def watch_elapsed_seconds(self, _value: float) -> None:
        self._refresh()

    def watch_tool_name(self, _value: str) -> None:
        self._refresh()

    def watch_usage_summary(self, _value: str) -> None:
        self._refresh()

    def watch_model_label(self, _value: str) -> None:
        self._refresh()

    def watch_cwd_label(self, _value: str) -> None:
        self._refresh()

    def watch_branch_label(self, _value: str) -> None:
        self._refresh()

    def watch_tokens_label(self, _value: str) -> None:
        self._refresh()

    def watch_cost_label(self, _value: str) -> None:
        self._refresh()

    def watch_permission_mode(self, _value: str) -> None:
        self._refresh()

    def watch_action_policy_mode(self, _value: str) -> None:
        self._refresh()

    def watch_custom_label(self, _value: str) -> None:
        self._refresh()

    def watch_goal_loop_label(self, _value: str) -> None:
        self._refresh()

    def watch_agent_label(self, _value: str) -> None:
        self._refresh()

    def watch_queued_count(self, _value: int) -> None:
        self._refresh()

    def watch_input_state(self, _value: str) -> None:
        self._refresh()

    def watch_tokens_severity(self, _value: str) -> None:
        self._refresh()

    def _refresh(self) -> None:
        try:
            label = self.query_one("#focus-status-hints", Label)
        except QueryError:
            return
        label.update(self._text())

    def _text(self) -> str:
        elapsed = self._format_elapsed(self.elapsed_seconds)
        if self.state == "tool":
            tool = self.tool_name or "tool"
            return self._compose_busy_text(f"⚙ {tool}", elapsed)
        if self.state == "responding":
            return self._compose_busy_text("● responding", elapsed)
        return self._compose_idle_text()

    def _compose_busy_text(self, label: str, elapsed: str) -> str:
        segments = [label, elapsed]
        segments.extend(self._rich_status_segments(include_agent=False))
        if self.queued_count:
            segments.append(f"queued: {self.queued_count}")
        segments.append("Esc cancel")
        return _SEGMENT_SEP.join(segments)

    def _compose_idle_text(self) -> str:
        """Return the idle-state line per spec §7.1 ordering."""
        segments = self._rich_status_segments(include_agent=True)
        prefix = _SEGMENT_SEP.join(segments)
        suffix = _INPUT_STATE_HINTS.get(self.input_state, _IDLE_HINTS)
        if self.usage_summary:
            suffix = f"{suffix}   {self.usage_summary}"
        if prefix:
            return f"{prefix}   {suffix}"
        return suffix

    def _rich_status_segments(self, *, include_agent: bool) -> list[str]:
        """Return stable runtime/status segments shared by idle and busy states."""
        segments: list[str] = []
        if include_agent and self.agent_label:
            segments.append(f"◆ {self.agent_label}")
        if self.model_label:
            segments.append(f"model: {self.model_label}")
        if self.cwd_label:
            segments.append(f"cwd: {self.cwd_label}")
        if self.branch_label:
            segments.append(f"git: {self.branch_label}")
        if self.tokens_label:
            tokens_text = f"tokens: {self.tokens_label}"
            if self.tokens_severity == TOKENS_SEVERITY_DANGER:
                tokens_text = f"[bold red]{tokens_text} {_CONTEXT_DANGER_GLYPH}[/]"
            elif self.tokens_severity == TOKENS_SEVERITY_WARN:
                tokens_text = f"[bold yellow]{tokens_text} {_CONTEXT_WARN_GLYPH}[/]"
            segments.append(tokens_text)
        if self.cost_label:
            segments.append(f"cost: {self.cost_label}")
        permission_label = format_permission_status_label(
            permission_mode=self.permission_mode,
            action_policy_mode=self.action_policy_mode,
        )
        if permission_label:
            segments.append(f"permissions: {permission_label}")
        if self.custom_label:
            segments.append(f"status: {self.custom_label}")
        if self.goal_loop_label:
            segments.append(self.goal_loop_label)
        return segments

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        if seconds <= 0:
            return "0s"
        if seconds < 60:
            return f"{int(seconds)}s"
        minutes, rem = divmod(int(seconds), 60)
        return f"{minutes}m{rem:02d}s"
