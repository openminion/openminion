from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PairCreateResult:
    token: str
    token_hint: str
    token_hash_prefix: str
    expires_at_ts: int
    scopes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PairingHandleResult:
    handled: bool
    reply_text: str | None = None
