from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar


def format_chat_timestamp(created_at: str, *, now: datetime | None = None) -> str:
    value = str(created_at or "").strip()
    if not value:
        return ""
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return value[11:16] if len(value) >= 16 else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local_dt = parsed.astimezone()
    reference = (
        now.astimezone(local_dt.tzinfo)
        if now is not None
        else datetime.now(local_dt.tzinfo)
    )
    age_seconds = int((reference - local_dt).total_seconds())
    if 0 <= age_seconds < 3600:
        age_minutes = age_seconds // 60
        if age_minutes <= 0:
            return "just now"
        return f"{age_minutes}m ago"
    if local_dt.date() == reference.date():
        return local_dt.strftime("%H:%M")
    if (reference.date() - local_dt.date()).days == 1:
        return local_dt.strftime("yesterday %H:%M")
    return local_dt.strftime("%b %d %H:%M")


class MessageKind(str, Enum):
    USER = "user"
    AGENT = "agent"
    TOOL = "tool"
    SYSTEM = "system"
    ERROR = "error"


@dataclass
class ToolEvent:
    tool_name: str
    args: dict[str, Any]
    content: str
    content_type: str = "text"
    duration_ms: int | None = None
    exit_code: int | None = None
    truncated: bool = False
    full_content: str = ""
    call_id: str = ""
    state: str = ""
    model_tool_name: str = ""
    runtime_tool_name: str = ""
    runtime_binding_id: str = ""
    runtime_fallback_used: bool = False
    runtime_fallback_chain: list[str] | None = None
    runtime_resolution_source: str = ""
    fallback_index: int | None = None

    def __post_init__(self) -> None:
        self.tool_name = str(self.tool_name or "").strip()
        self.args = dict(self.args or {})
        self.content = str(self.content or "")
        self.content_type = str(self.content_type or "text").strip() or "text"
        self.call_id = str(self.call_id or "").strip()
        if not self.full_content:
            self.full_content = self.content
        self.state = str(self.state or "").strip()
        self.model_tool_name = str(self.model_tool_name or "").strip()
        self.runtime_tool_name = str(self.runtime_tool_name or "").strip()
        self.runtime_binding_id = str(self.runtime_binding_id or "").strip()
        self.runtime_resolution_source = str(
            self.runtime_resolution_source or ""
        ).strip()
        if self.runtime_fallback_chain is not None:
            self.runtime_fallback_chain = [
                str(item or "").strip()
                for item in list(self.runtime_fallback_chain)
                if str(item or "").strip()
            ] or None


@dataclass
class ChatMessage:
    kind: MessageKind
    sender: str
    body: str
    tool_result: str | None = None
    tool_event: ToolEvent | None = None
    retryable_error: bool = False
    show_header: bool = True
    timestamp: str = ""
    created_at: str = ""
    msg_id: str = ""

    _counter: ClassVar[int] = 0

    def __post_init__(self) -> None:
        if not self.msg_id:
            ChatMessage._counter += 1
            self.msg_id = f"msg-{ChatMessage._counter}"
        if not self.created_at and self.kind != MessageKind.SYSTEM:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def display_timestamp(self) -> str:
        return self.timestamp or format_chat_timestamp(self.created_at)


__all__ = ["ChatMessage", "MessageKind", "ToolEvent", "format_chat_timestamp"]
