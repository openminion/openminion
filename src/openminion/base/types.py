from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import uuid4

from openminion.base.time import utc_now


@runtime_checkable
class MessageStats(Protocol):
    @property
    def has_any_data(self) -> bool: ...

    def as_payload(self) -> dict[str, int]: ...


@dataclass
class Message:
    channel: str
    target: str
    body: str
    metadata: dict[str, str] = field(default_factory=dict)
    stats: MessageStats | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class AgentResponse:
    text: str
    channel: str
    target: str
    metadata: dict[str, str] = field(default_factory=dict)
