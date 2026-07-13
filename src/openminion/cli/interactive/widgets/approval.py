from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label


class ToolApprovalWidget(Widget):
    """Three-way inline approval per spec §9.1."""

    SCOPE_ONCE = "once"
    SCOPE_SESSION = "session"

    class Approved(Message):
        def __init__(self, tool_name: str, *, scope: str = "once") -> None:
            super().__init__()
            self.tool_name = tool_name
            self.scope = str(scope or "once").strip().lower() or "once"

    class Denied(Message):
        def __init__(self, tool_name: str) -> None:
            super().__init__()
            self.tool_name = tool_name

    class AllowAll(Message):
        """Compatibility alias for `Approved(scope="session")`."""

        def __init__(self, tool_name: str) -> None:
            super().__init__()
            self.tool_name = tool_name

    BINDINGS = [
        ("a", "approve_once", "Allow once"),
        ("s", "approve_session", "Session allow"),
        ("d", "deny", "Deny"),
        ("escape", "deny", "Deny"),
        ("y", "approve_once", "Allow once"),
        ("enter", "approve_once", "Allow once"),
        ("n", "deny", "Deny"),
    ]

    can_focus = True

    def __init__(
        self, tool_name: str, args: dict[str, Any], *, allow_all: bool
    ) -> None:
        super().__init__(classes="focus-approval")
        self._tool_name = str(tool_name or "").strip() or "tool"
        self._args = dict(args or {})
        self._allow_all = bool(allow_all)

    def compose(self) -> ComposeResult:
        yield Label(
            f"{self._tool_name} requires approval",
            classes="focus-approval-title",
        )
        yield Label(self._args_summary(), classes="focus-approval-args")
        if self._allow_all:
            hint = r"\[A] Allow once   \[S] Session allow   \[D] Deny"
        else:
            hint = r"\[A] Allow once   \[D] Deny"
        yield Label(hint, classes="focus-approval-hint")

    def action_approve_once(self) -> None:
        self.post_message(self.Approved(self._tool_name, scope=self.SCOPE_ONCE))

    def action_approve_session(self) -> None:
        if not self._allow_all:
            self.action_approve_once()
            return
        self.post_message(self.Approved(self._tool_name, scope=self.SCOPE_SESSION))
        self.post_message(self.AllowAll(self._tool_name))

    def action_deny(self) -> None:
        self.post_message(self.Denied(self._tool_name))

    def action_approve(self) -> None:
        self.action_approve_once()

    def action_allow_all(self) -> None:
        self.action_approve_session()

    def _args_summary(self) -> str:
        if not self._args:
            return "{}"
        try:
            return json.dumps(self._args, sort_keys=True)
        except (TypeError, ValueError):
            return str(self._args)
