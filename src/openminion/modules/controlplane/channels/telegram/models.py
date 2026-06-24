from dataclasses import dataclass, field
from typing import Any

from .constants import ACCESS_REASON_OK


@dataclass(frozen=True)
class TelegramUser:
    id: int
    username: str | None = None
    display: str | None = None


@dataclass(frozen=True)
class TelegramInboundEnvelope:
    update_id: int
    raw_type: str
    chat_id: int
    message_id: int
    text: str
    from_user: TelegramUser
    chat_type: str
    topic_id: int | None = None
    callback_query_id: str | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    raw_update: dict[str, Any] = field(default_factory=dict)

    @property
    def is_group(self) -> bool:
        return self.chat_type in {"group", "supergroup"}

    @property
    def is_topic(self) -> bool:
        return self.topic_id is not None


@dataclass(frozen=True)
class ControlEvent:
    channel: str
    conversation_id: str
    thread_id: str | None
    message_id: str
    from_user: dict[str, Any]
    text: str
    attachments: list[dict[str, Any]]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class TelegramReplyTarget:
    chat_id: int
    message_id: int
    topic_id: int | None = None


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str = ACCESS_REASON_OK


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    sent_messages: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class PairTokenIssue:
    token: str
    token_hint: str
    token_hash_prefix: str
    expires_at_ts: int
    scopes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PairConsumeResult:
    ok: bool
    reason: str
    token_hint: str = ""
    token_hash_prefix: str = ""
    scopes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PairingHandleResult:
    handled: bool
    reply_text: str | None = None
