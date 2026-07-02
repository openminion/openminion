"""Typed Slack wire models used by the controlplane adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SlackInboundEnvelope:
    team_id: str
    channel_id: str
    user_id: str
    text: str
    ts: str
    event_id: str
    event_type: str
    channel_type: str = ""
    thread_ts: str | None = None
    bot_id: str | None = None
    bot_user_id: str | None = None
    subtype: str | None = None
    blocks: tuple[dict[str, Any], ...] = ()
    raw_event: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SlackReplyTarget:
    channel_id: str
    thread_ts: str | None = None
    reply_to: str | None = None


@dataclass(frozen=True)
class SlackEventCallback:
    team_id: str
    event_id: str
    event: dict[str, Any]
    raw: dict[str, Any]


@dataclass(frozen=True)
class SlackSlashCommandEnvelope:
    team_id: str
    channel_id: str
    user_id: str
    command: str
    text: str
    response_url: str | None = None
    trigger_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SlackDeliveryResult:
    ok: bool
    message_ts: str | None = None
    channel_id: str | None = None
    chunks_sent: int = 0
