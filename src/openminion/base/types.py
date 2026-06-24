from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from openminion.base.time import utc_now

if TYPE_CHECKING:
    from openminion.services.stats.types import RunStats


@dataclass
class Message:
    channel: str
    target: str
    body: str
    metadata: dict[str, str] = field(default_factory=dict)
    stats: RunStats | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class AgentResponse:
    text: str
    channel: str
    target: str
    metadata: dict[str, str] = field(default_factory=dict)
