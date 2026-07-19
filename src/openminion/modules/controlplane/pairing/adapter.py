from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from openminion.modules.controlplane.contracts.models import InboundMessage


@dataclass(frozen=True)
class PairingAttempt:
    """Channel-normalized pairing attempt produced by a channel adapter."""

    channel: str
    token: str
    account_id: str
    chat_key: str
    chat_type: str
    extra: dict[str, Any] = field(default_factory=dict)


class PairingAdapter(Protocol):
    """Wire-format-only pairing adapter for a concrete channel."""

    @property
    def channel_id(self) -> str: ...

    @property
    def account_namespace(self) -> str: ...

    def extract_pairing_attempt(
        self,
        inbound: InboundMessage,
        *,
        channel_context: dict[str, Any] | None = None,
    ) -> PairingAttempt | None: ...

    def format_pairing_hint(self, token: str, *, ttl_seconds: int) -> str: ...

    def format_success_reply(self) -> str: ...

    def format_failure_reply(self, reason: str) -> str: ...
