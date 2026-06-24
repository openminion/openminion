from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from ..runtime.interaction import InteractionChannel


@dataclass(frozen=True)
class AttachmentInput:
    name: str
    mime: str
    data: bytes | None = None
    url: str | None = None


@dataclass(frozen=True)
class AttachmentRef:
    kind: str
    name: str
    mime: str | None = None
    size_bytes: int | None = None
    source: str = "artifact"
    ref: str = ""


@dataclass(frozen=True)
class AuthContext:
    role: str = "user"
    scopes: tuple[str, ...] = ()
    principal_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UIButton:
    id: str
    text: str
    action: str


@dataclass(frozen=True)
class UIReaction:
    kind: str
    value: str


@dataclass(frozen=True)
class UIProgress:
    run_id: str
    stage: str
    pct: int | None = None
    message: str = ""
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass(frozen=True)
class UIHints:
    buttons: list[UIButton] = field(default_factory=list)
    reaction: UIReaction | None = None
    progress: UIProgress | None = None


@dataclass(frozen=True)
class InboundMessage:
    user_key: str
    chat_key: str
    text: str
    channel: str = "cli"
    thread_key: str | None = None
    attachments: list[AttachmentInput | AttachmentRef] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    # Canonical V1 envelope fields.
    inbound_id: str | None = None
    channel_message_id: str | None = None
    chat_id: str | None = None
    user_id: str | None = None
    thread_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reply_to: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    auth: AuthContext | None = None


@dataclass(frozen=True)
class ResolvedContext:
    user_key: str
    chat_key: str
    session_id: str
    agent_id: str
    role: str
    trace_id: str
    span_id: str
    ui: "InteractionChannel | None" = None
    wizard_session_id: str | None = None


@dataclass(frozen=True)
class DeliveryContext:
    channel: str
    chat_id: str
    thread_id: str | None = None
    reply_to: str | None = None
    outbox_id: str | None = None


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None


@dataclass(frozen=True)
class ParsedCommand:
    canonical: str
    original_text: str
    args: list[str] = field(default_factory=list)


class CommandParser(Protocol):
    contract_version: str

    def parse(self, text: str) -> ParsedCommand | None: ...


@dataclass(frozen=True)
class OutboundMessage:
    outbound_id: str
    channel: str
    chat_id: str
    text: str
    thread_id: str | None = None
    reply_to: str | None = None
    attachments: list[AttachmentRef] = field(default_factory=list)
    ui: UIHints = field(default_factory=UIHints)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Route:
    session_id: str
    agent_id: str
    run_mode: str = "sync"
    channel_context: dict[str, str] = field(default_factory=dict)
    permissions: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunRequest:
    run_id: str
    session_id: str
    agent_id: str
    user_event_id: str | None
    mode: str = "sync"
    channel_return: dict[str, Any] = field(default_factory=dict)
    deadline_s: int | None = None


class SessionClient(Protocol):
    contract_version: str

    def create_session(self, meta: dict[str, Any] | None = None) -> str: ...

    def append_turn(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        attachments: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str: ...


class BrainClient(Protocol):
    contract_version: str

    def run(
        self,
        *,
        session_id: str,
        agent_id: str,
        user_text: str | None,
        attachment_refs: list[str],
        trace_id: str,
    ) -> dict[str, Any]: ...


class OutboundSender(Protocol):  # pragma: no cover - adapters will implement
    contract_version: str

    def __call__(self, payload: dict[str, Any]) -> None: ...
