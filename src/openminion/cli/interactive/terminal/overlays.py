from __future__ import annotations

import asyncio
from typing import Any, Iterable, Literal

from prompt_toolkit import PromptSession
from rich.console import Console
from rich.text import Text

from openminion.cli.presentation.styles import StyleToken
from openminion.cli.presentation.markers import token_rich_style

_ERR_STYLE = token_rich_style(StyleToken.ERROR)


class TerminalOverlayPresenter:
    """Inline overlays for terminal flow."""

    def __init__(
        self,
        *,
        console: Console,
        prompt_session: PromptSession | None = None,
    ) -> None:
        self._console = console
        self._session = prompt_session or PromptSession()

    def present_resume_picker(self, sessions: Iterable[Any]) -> str | None:
        return asyncio.run(self._present_resume_picker_async(sessions))

    async def _present_resume_picker_async(self, sessions: Iterable[Any]) -> str | None:
        items = list(sessions)
        if not items:
            self._console.print(Text("(no resumable sessions)", style="dim italic"))
            return None
        self._console.print(Text("Resume which session?", style="bold"))
        for i, item in enumerate(items, start=1):
            label = _session_label(item)
            self._console.print(f"  {i}. {label}")
        try:
            text = await self._session.prompt_async("Number (Enter to cancel): ")
        except (EOFError, KeyboardInterrupt):
            return None
        choice = (text or "").strip()
        if not choice:
            return None
        try:
            idx = int(choice)
        except ValueError:
            self._console.print(Text(f"(invalid number: {choice!r})", style=_ERR_STYLE))
            return None
        if idx < 1 or idx > len(items):
            self._console.print(Text(f"(out of range: {idx})", style=_ERR_STYLE))
            return None
        return _session_id(items[idx - 1])

    def present_approval(self, prompt: str) -> Literal["allow", "deny", "always"]:
        return asyncio.run(self.present_approval_async(prompt))

    async def present_approval_async(
        self, prompt: str
    ) -> Literal["allow", "deny", "always"]:
        self._console.print(Text(prompt, style="bold"))
        try:
            text = await self._session.prompt_async("[y]es / [N]o / [a]lways: ")
        except (EOFError, KeyboardInterrupt):
            return "deny"
        norm = (text or "").strip().lower()
        if norm in ("y", "yes"):
            return "allow"
        if norm in ("a", "always"):
            return "always"
        return "deny"

    def present_completion(self, message: str) -> str:
        return asyncio.run(self._present_completion_async(message))

    async def _present_completion_async(self, message: str) -> str:
        self._console.print(Text(message))
        try:
            text = await self._session.prompt_async("> ")
        except (EOFError, KeyboardInterrupt):
            return ""
        return str(text or "").strip()

    def present_confirm(self, prompt: str, *, default: bool = False) -> bool:
        return asyncio.run(self.present_confirm_async(prompt, default=default))

    async def present_confirm_async(
        self, prompt: str, *, default: bool = False
    ) -> bool:
        self._console.print(Text(prompt, style="bold"))
        suffix = "[Y/n]: " if default else "[y/N]: "
        try:
            text = await self._session.prompt_async(suffix)
        except (EOFError, KeyboardInterrupt):
            return False
        normalized = str(text or "").strip().lower()
        if not normalized:
            return default
        return normalized in {"y", "yes"}


def _session_label(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("label") or item.get("name") or item.get("id") or item)
    for attr in ("label", "name", "id"):
        val = getattr(item, attr, None)
        if val:
            return str(val)
    return str(item)


def _session_id(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("id") or item.get("name") or item)
    for attr in ("id", "name"):
        val = getattr(item, attr, None)
        if val:
            return str(val)
    return str(item)
