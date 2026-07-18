from __future__ import annotations

import logging
import re
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol

from openminion.modules.controlplane.constants import (
    PRINCIPAL_BINDING_STATUS_ACTIVE,
)
from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.runtime.audit import emit_audit_event

from .adapter import PairingAdapter, PairingAttempt
from .policy import PAIRING_MODE_OFF, PairingPolicy
from .results import PairCreateResult, PairingHandleResult
from .store import ControlPlanePairingStore, now_ts

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ControlPlanePairingBridge(Protocol):
    def upsert_pairing(
        self,
        *,
        channel: str,
        chat_id: str,
        user_id: str,
        session_id: str,
        status: str = PRINCIPAL_BINDING_STATUS_ACTIVE,
        scopes: list[str] | tuple[str, ...] | None = None,
        note: str | None = None,
        pairing_id: str | None = None,
    ) -> str: ...

    def resolve_session(self, user_key: str, chat_key: str) -> str: ...


class RecentPairAttemptsLRU:
    def __init__(self, *, max_size: int = 2048) -> None:
        self._max_size = max(128, int(max_size))
        self._seen: OrderedDict[str, int] = OrderedDict()

    def bump(self, token_prefix: str) -> int:
        current = int(self._seen.get(token_prefix, 0)) + 1
        self._seen[token_prefix] = current
        self._seen.move_to_end(token_prefix)
        if len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
        return current


@dataclass
class ControlPlanePairingService:
    policy: PairingPolicy
    store: ControlPlanePairingStore
    adapter: PairingAdapter
    bridge_store: ControlPlanePairingBridge | None = None
    legacy_store: Any | None = None
    audit_logger: object | None = None
    logger: logging.Logger | None = None
    lru: RecentPairAttemptsLRU | None = None

    def __post_init__(self) -> None:
        self._log = self.logger or logging.getLogger(__name__)
        self._lru = self.lru or RecentPairAttemptsLRU()

    def issue_token(
        self,
        *,
        expected_account_id: str | None,
        expected_chat_key: str | None,
        token_ttl_seconds: int | None = None,
        scopes: list[str] | None = None,
        token: str | None = None,
    ) -> PairCreateResult:
        ttl = token_ttl_seconds or self.policy.token_ttl_seconds
        scoped = scopes or list(self.policy.default_scopes)
        issued = self.store.issue_token(
            channel=self.adapter.channel_id,
            expected_account_id=expected_account_id,
            expected_chat_key=expected_chat_key,
            scopes=scoped,
            token=token,
            ttl_seconds=ttl,
            hash_pepper=self.policy.hash_pepper,
        )
        self._audit(
            "cp.pairing.token.issued",
            outcome="ok",
            channel=self.adapter.channel_id,
            token_hint=issued.get("token_hint"),
            token_hash_prefix=issued.get("token_hash_prefix"),
            expires_at_ts=issued.get("expires_at_ts"),
        )
        return PairCreateResult(
            token=str(issued["token"]),
            token_hint=str(issued["token_hint"]),
            token_hash_prefix=str(issued["token_hash_prefix"]),
            expires_at_ts=int(issued["expires_at_ts"]),
            scopes=list(issued.get("scopes") or []),
        )

    def handle_pairing_attempt(
        self,
        inbound: InboundMessage,
        *,
        channel_context: dict[str, Any] | None = None,
    ) -> PairingHandleResult:
        if not self.policy.enabled or self.policy.mode == PAIRING_MODE_OFF:
            return PairingHandleResult(handled=False)

        attempt = self.adapter.extract_pairing_attempt(
            inbound, channel_context=channel_context
        )
        if attempt is None:
            return PairingHandleResult(handled=False)

        if attempt.chat_type != "private" and not self.policy.allow_in_groups:
            return PairingHandleResult(
                handled=True,
                reply_text="Pairing is only allowed in direct messages.",
            )

        if not _TOKEN_RE.fullmatch(attempt.token):
            self._record_attempt(attempt, "invalid_format")
            return self._rejected(attempt, "invalid_format")

        lru_count = self._lru.bump(attempt.token[:12])
        if lru_count > 8:
            self._record_attempt(attempt, "lru_limited")
            return PairingHandleResult(
                handled=True,
                reply_text="Too many pairing attempts. Try again shortly.",
            )

        if self._is_rate_limited(attempt):
            self._record_attempt(attempt, "rate_limited")
            return PairingHandleResult(
                handled=True,
                reply_text="Too many pairing attempts. Try again shortly.",
            )

        consume = self.store.consume_pair_token(
            channel=attempt.channel,
            token=attempt.token,
            consumer_account_id=attempt.account_id,
            consumer_chat_key=attempt.chat_key,
            hash_pepper=self.policy.hash_pepper,
        )
        if (
            not bool(consume.get("ok"))
            and consume.get("reason") == "invalid_token"
            and self.legacy_store is not None
        ):
            consume = self._try_legacy_redeem(attempt, consume)

        reason = str(consume.get("reason") or "unknown")
        self._record_attempt(attempt, reason)
        if bool(consume.get("ok")):
            self._bridge_pairing_to_controlplane(attempt=attempt, consume=consume)
            self._audit(
                "cp.pairing.token.consumed",
                outcome="ok",
                channel=attempt.channel,
                token_hint=consume.get("token_hint"),
                token_hash_prefix=consume.get("token_hash_prefix"),
            )
            return PairingHandleResult(
                handled=True, reply_text=self.adapter.format_success_reply()
            )

        self._audit(
            "cp.pairing.token.rejected",
            outcome=reason,
            channel=attempt.channel,
            token_hint=consume.get("token_hint"),
            token_hash_prefix=consume.get("token_hash_prefix"),
        )
        return self._rejected(attempt, reason)

    def _try_legacy_redeem(
        self, attempt: PairingAttempt, consume: dict[str, Any]
    ) -> dict[str, Any]:
        if attempt.channel != "telegram":
            return consume
        user_id = attempt.extra.get("telegram_user_id")
        chat_id = attempt.extra.get("telegram_chat_id")
        legacy_store = self.legacy_store
        if legacy_store is None or user_id is None or chat_id is None:
            return consume
        legacy = legacy_store.consume_pair_token(
            token=attempt.token,
            user_id=int(user_id),
            chat_id=int(chat_id),
            topic_id=attempt.extra.get("topic_id"),
            hash_pepper=self.policy.hash_pepper,
        )
        if not getattr(legacy, "ok", False):
            return consume
        self._audit(
            "cp.pairing.legacy_redeem",
            outcome="ok",
            channel=attempt.channel,
            token_hint=getattr(legacy, "token_hint", ""),
            token_hash_prefix=getattr(legacy, "token_hash_prefix", ""),
        )
        return {
            "ok": True,
            "reason": getattr(legacy, "reason", "paired"),
            "token_hint": getattr(legacy, "token_hint", ""),
            "token_hash_prefix": getattr(legacy, "token_hash_prefix", ""),
            "scopes": list(getattr(legacy, "scopes", []) or []),
        }

    def _is_rate_limited(self, attempt: PairingAttempt) -> bool:
        since_ts = now_ts() - max(1, int(self.policy.attempt_window_seconds))
        return (
            self.store.count_recent_attempts(
                channel=attempt.channel,
                account_id=attempt.account_id,
                since_ts=since_ts,
            )
            >= self.policy.max_attempts_per_user
            or self.store.count_recent_attempts_for_chat(
                channel=attempt.channel,
                chat_key=attempt.chat_key,
                since_ts=since_ts,
            )
            >= self.policy.max_attempts_per_chat
        )

    def _record_attempt(self, attempt: PairingAttempt, outcome: str) -> None:
        self.store.record_attempt(
            channel=attempt.channel,
            account_id=attempt.account_id,
            chat_key=attempt.chat_key,
            token=attempt.token,
            outcome=outcome,
            hash_pepper=self.policy.hash_pepper,
            detail={"chat_type": attempt.chat_type, **attempt.extra},
        )
        if attempt.channel != "telegram" or self.legacy_store is None:
            return
        user_id = attempt.extra.get("telegram_user_id")
        chat_id = attempt.extra.get("telegram_chat_id")
        if user_id is None or chat_id is None:
            return
        try:
            self.legacy_store.record_pair_attempt(
                token=attempt.token,
                user_id=int(user_id),
                chat_id=int(chat_id),
                outcome=outcome,
                hash_pepper=self.policy.hash_pepper,
            )
        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            self._log.warning("legacy pairing attempt record failed: %s", exc)

    def _bridge_pairing_to_controlplane(
        self, *, attempt: PairingAttempt, consume: dict[str, Any]
    ) -> None:
        bridge = self.bridge_store
        if bridge is None:
            return

        user_key = str(attempt.extra.get("session_user_key") or attempt.account_id)
        chat_key = str(attempt.extra.get("session_chat_key") or attempt.chat_key)
        session_id = f"{attempt.channel}-pair:{attempt.chat_key}"
        try:
            session_id = str(
                bridge.resolve_session(user_key=user_key, chat_key=chat_key)
            )
        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            self._log.warning("pairing bridge resolve_session failed: %s", exc)

        scopes = list(consume.get("scopes") or self.policy.default_scopes)
        try:
            bridge.upsert_pairing(
                channel=attempt.channel,
                chat_id=str(attempt.extra.get("subject_id") or attempt.chat_key),
                user_id=str(attempt.extra.get("user_id") or attempt.account_id),
                session_id=session_id,
                scopes=scopes,
                note=f"{attempt.channel}_pair_bridge",
            )
        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            self._log.warning(
                "pairing bridge upsert failed channel=%s: %s",
                attempt.channel,
                exc,
            )

    def _rejected(self, attempt: PairingAttempt, reason: str) -> PairingHandleResult:
        return PairingHandleResult(
            handled=True,
            reply_text=self.adapter.format_failure_reply(reason),
        )

    def _audit(self, event_type: str, *, outcome: str, **details: object) -> None:
        if self.audit_logger is None:
            return
        if hasattr(self.audit_logger, "emit"):
            self.audit_logger.emit(
                event_type,
                outcome=outcome,
                details=dict(details),
            )
            return
        emit_audit_event(self.audit_logger, event_type, outcome=outcome, **details)
