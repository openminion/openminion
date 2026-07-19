from collections.abc import Callable
from typing import Any, Iterable, Literal, Protocol, runtime_checkable

from openminion.cli.presentation.models import ChatMessage, ToolEvent


__all__ = [
    "Composer",
    "OverlayPresenter",
    "StatusLine",
    "TranscriptSink",
    "TurnHandleProtocol",
]


@runtime_checkable
class TurnHandleProtocol(Protocol):
    def append_token(self, s: str) -> None: ...

    def append_tool_block(self, event: ToolEvent) -> None: ...

    def complete(self, final_text: str | None = None) -> None: ...


@runtime_checkable
class TranscriptSink(Protocol):
    def begin_turn(
        self,
        role: Literal["user", "assistant"] = "assistant",
        *,
        footer_provider: Callable[[], str] | None = None,
    ) -> TurnHandleProtocol: ...

    def push_message(self, message: ChatMessage) -> Any: ...

    def set_messages(self, messages: list[ChatMessage]) -> None: ...

    def clear_messages(self) -> None: ...

    def filter_messages(self, query: str) -> None: ...

    def copy_selected_message(self) -> str | None: ...

    def copy_last_copyable_message(self) -> str | None: ...

    def drop_message(self, msg_id: str) -> bool: ...


@runtime_checkable
class Composer(Protocol):
    def set_resumed(self, is_resumed: bool) -> None: ...

    def set_disabled(self, disabled: bool) -> None: ...

    def focus_input(self) -> None: ...

    def toggle_multiline(self) -> None: ...


@runtime_checkable
class StatusLine(Protocol):
    def set_state(self, **segments: Any) -> None: ...


@runtime_checkable
class OverlayPresenter(Protocol):
    def present_resume_picker(self, sessions: Iterable[Any]) -> str | None: ...

    def present_approval(self, prompt: str) -> Literal["allow", "deny", "always"]: ...

    def present_completion(self, message: str) -> str: ...
