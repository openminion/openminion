from typing import Any, Iterable, Literal

from prompt_toolkit import PromptSession
from rich.console import Console
from rich.text import Text

from openminion.cli.presentation.styles import StyleToken
from openminion.cli.presentation.markers import token_rich_style
from openminion.cli.presentation.permissions import PERMISSION_MENU_CHOICES
from openminion.modules.runtime.sync import run_async_compat


class TerminalOverlayPresenter:
    """Inline overlays for terminal flow."""

    def __init__(
        self,
        *,
        console: Console,
        prompt_session: PromptSession[str] | None = None,
    ) -> None:
        self._console = console
        self._session = prompt_session or PromptSession()

    def present_resume_picker(self, sessions: Iterable[Any]) -> str | None:
        return run_async_compat(self._present_resume_picker_async(sessions))

    async def _present_resume_picker_async(self, sessions: Iterable[Any]) -> str | None:
        items = list(sessions)
        if not items:
            self._console.print(Text("(no resumable sessions)", style="dim italic"))
            return None
        choices = [
            "Resume which session?",
            *(
                f"  {i}. {_session_label(item)}"
                for i, item in enumerate(items, start=1)
            ),
        ]
        try:
            text = await self._session.prompt_async(
                "\n".join(choices) + "\nNumber (Enter to cancel): "
            )
        except (EOFError, KeyboardInterrupt):
            return None
        choice = (text or "").strip()
        if not choice:
            return None
        try:
            idx = int(choice)
        except ValueError:
            self._console.print(
                Text(
                    f"(invalid number: {choice!r})",
                    style=token_rich_style(StyleToken.ERROR),
                )
            )
            return None
        if idx < 1 or idx > len(items):
            self._console.print(
                Text(
                    f"(out of range: {idx})",
                    style=token_rich_style(StyleToken.ERROR),
                )
            )
            return None
        return _session_id(items[idx - 1])

    def present_approval(self, prompt: str) -> Literal["allow", "deny", "always"]:
        return run_async_compat(self.present_approval_async(prompt))

    async def present_approval_async(
        self,
        prompt: str,
        *,
        always_label: str = "Always",
    ) -> Literal["allow", "deny", "always"]:
        try:
            text = await self._session.prompt_async(
                f"{prompt}\n[y] Allow once / [N] Deny (default) / [a] {always_label}: "
            )
        except (EOFError, KeyboardInterrupt):
            return "deny"
        norm = (text or "").strip().lower()
        if norm in ("y", "yes"):
            return "allow"
        if norm in ("a", "always"):
            return "always"
        return "deny"

    async def present_permission_picker_async(self) -> str | None:
        lines = [
            "Choose permissions:",
            *(
                f"  {index}. {choice.label} — {choice.description}"
                for index, choice in enumerate(PERMISSION_MENU_CHOICES, start=1)
            ),
        ]
        try:
            text = await self._session.prompt_async(
                "\n".join(lines) + "\nNumber (Enter to cancel): "
            )
        except (EOFError, KeyboardInterrupt):
            return None
        raw = str(text or "").strip()
        if not raw:
            return None
        try:
            return str(PERMISSION_MENU_CHOICES[int(raw) - 1].choice_id)
        except (ValueError, IndexError):
            self._console.print(
                Text(
                    "(invalid permission choice)",
                    style=token_rich_style(StyleToken.ERROR),
                )
            )
            return None

    def present_completion(self, message: str) -> str:
        return run_async_compat(self._present_completion_async(message))

    async def _present_completion_async(self, message: str) -> str:
        try:
            text = await self._session.prompt_async(f"{message}\n> ")
        except (EOFError, KeyboardInterrupt):
            return ""
        return str(text or "").strip()

    def present_confirm(self, prompt: str, *, default: bool = False) -> bool:
        return run_async_compat(self.present_confirm_async(prompt, default=default))

    async def present_confirm_async(
        self, prompt: str, *, default: bool = False
    ) -> bool:
        suffix = "[Y/n]: " if default else "[y/N]: "
        try:
            text = await self._session.prompt_async(f"{prompt}\n{suffix}")
        except (EOFError, KeyboardInterrupt):
            return False
        normalized = str(text or "").strip().lower()
        if not normalized:
            return default
        return normalized in {"y", "yes"}

    async def present_prompt_async(
        self,
        prompt: str,
        *,
        secret: bool = False,
    ) -> str | None:
        try:
            text = await self._session.prompt_async(prompt, is_password=secret)
        except (EOFError, KeyboardInterrupt):
            return None
        return str(text or "").strip()


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
