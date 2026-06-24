from dataclasses import dataclass
from enum import Enum
from typing import Protocol


@dataclass(slots=True)
class PromptResponse:
    value: str
    cancelled: bool = False
    error: str | None = None


@dataclass(slots=True)
class ChoiceResponse:
    value: str
    index: int | None = None
    cancelled: bool = False
    error: str | None = None


@dataclass(slots=True)
class ConfirmResponse:
    confirmed: bool
    cancelled: bool = False
    error: str | None = None


@dataclass(slots=True)
class MessageResponse:
    delivered: bool
    error: str | None = None


@dataclass(slots=True)
class Option:
    value: str
    label: str
    description: str | None = None

    def __str__(self) -> str:
        if self.description:
            return f"{self.label} - {self.description}"
        return self.label


class InteractionMode(Enum):
    """Describes the interaction mode for adaptive UI."""

    TERMINAL = "terminal"
    CHAT = "chat"
    TELEGRAM = "telegram"
    WEB = "web"
    UNKNOWN = "unknown"


_CHANNEL_MODE_COMPAT_MAP: dict[str, InteractionMode] = {
    "terminal": InteractionMode.TERMINAL,
    "cli": InteractionMode.TERMINAL,
    "chat": InteractionMode.CHAT,
    "discord": InteractionMode.CHAT,
    "slack": InteractionMode.CHAT,
    "telegram": InteractionMode.TELEGRAM,
    "web": InteractionMode.WEB,
}


def resolve_interaction_mode(
    channel_name: str | None,
    *,
    default: InteractionMode = InteractionMode.UNKNOWN,
) -> InteractionMode:
    """Resolve a channel name to the compatibility interaction mode enum."""
    normalized = str(channel_name or "").strip().lower()
    if not normalized:
        return default
    return _CHANNEL_MODE_COMPAT_MAP.get(normalized, default)


class InteractionChannel(Protocol):
    """Frontend-neutral protocol for wizard interaction flows."""

    async def prompt(
        self,
        message: str,
        default_value: str | None = None,
        hint: str | None = None,
    ) -> PromptResponse: ...

    async def choose(
        self,
        message: str,
        options: list[str | Option],
        default_index: int | None = None,
        allow_multiple: bool = False,
    ) -> ChoiceResponse: ...

    async def confirm(
        self, message: str, default: bool = True, danger: bool = False
    ) -> ConfirmResponse: ...

    async def message(
        self, content: str, title: str | None = None, style: str | None = None
    ) -> MessageResponse: ...

    async def diff(
        self, original: str, modified: str, title: str | None = None
    ) -> MessageResponse: ...

    async def progress(
        self, description: str, percent: float, details: str | None = None
    ) -> MessageResponse: ...

    def get_interaction_mode(self) -> InteractionMode: ...

    def supports_advanced_ui(self) -> bool: ...

    async def start_wizard_context(self, wizard_session_id: str) -> bool: ...

    async def end_wizard_context(self, wizard_session_id: str) -> bool: ...

    def is_cancel_requested(self) -> bool: ...

    async def cancel_wizard(self, message: str | None = None) -> bool: ...
