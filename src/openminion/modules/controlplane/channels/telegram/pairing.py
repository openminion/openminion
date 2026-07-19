from __future__ import annotations

import logging
import sqlite3
import warnings
from typing import Any, Protocol, cast

from openminion.modules.controlplane.channels.telegram.config import PairingConfig
from openminion.modules.controlplane.channels.telegram.interfaces import (
    TELEGRAM_INTERFACE_VERSION,
)
from openminion.modules.controlplane.channels.telegram.models import (
    PairTokenIssue,
    TelegramInboundEnvelope,
)
from openminion.modules.controlplane.channels.telegram.normalization import (
    to_control_event,
    to_inbound_message,
)
from openminion.modules.controlplane.channels.telegram.pairing_adapter import (
    TelegramPairingAdapter,
)
from openminion.modules.controlplane.channels.telegram.state import (
    TelegramPollStateStore,
)
from openminion.modules.controlplane.constants import (
    PRINCIPAL_BINDING_STATUS_ACTIVE,
)
from openminion.modules.controlplane.pairing import (
    ControlPlanePairingService,
    ControlPlanePairingStore,
    PairCreateResult,
    PairingHandleResult,
    PairingPolicy,
    RecentPairAttemptsLRU,
)


class _ControlPlanePairingStore(Protocol):
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


class _LegacyTelegramPairingStoreAdapter:
    def __init__(self, *, store: TelegramPollStateStore, config: PairingConfig) -> None:
        self._store = store
        self._config = config

    def issue_pair_token(
        self,
        *,
        channel: str,
        expected_account_id: str | None,
        expected_chat_key: str | None,
        scopes: list[str],
        token: str | None,
        ttl_seconds: int,
        hash_pepper: str | None = None,
    ) -> dict[str, Any]:
        issued = self._store.issue_pair_token(
            token=token,
            token_ttl_seconds=ttl_seconds,
            scopes=scopes,
            expected_user_id=_telegram_id_suffix(expected_account_id),
            expected_chat_id=_telegram_id_suffix(expected_chat_key),
            hash_pepper=hash_pepper,
        )
        return _issue_to_dict(issued)

    def consume_pair_token(
        self,
        *,
        channel: str,
        token: str,
        consumer_account_id: str,
        consumer_chat_key: str,
        hash_pepper: str | None = None,
    ) -> dict[str, Any]:
        consumed = self._store.consume_pair_token(
            token=token,
            user_id=int(_telegram_id_suffix(consumer_account_id) or 0),
            chat_id=int(_telegram_id_suffix(consumer_chat_key) or 0),
            topic_id=None,
            hash_pepper=hash_pepper,
        )
        return {
            "ok": consumed.ok,
            "reason": consumed.reason,
            "token_hint": consumed.token_hint,
            "token_hash_prefix": consumed.token_hash_prefix,
            "scopes": list(consumed.scopes or []),
        }

    def count_recent_pair_attempts(
        self, *, channel: str, account_id: str, since_ts: int
    ) -> int:
        del since_ts
        user_id = _telegram_id_suffix(account_id)
        if user_id is None:
            return 0
        return self._store.count_recent_attempts_for_user(
            user_id=user_id,
            window_seconds=self._config.attempt_window_seconds,
        )

    def count_recent_pair_attempts_for_chat(
        self, *, channel: str, chat_key: str, since_ts: int
    ) -> int:
        del since_ts
        chat_id = _telegram_id_suffix(chat_key)
        if chat_id is None:
            return 0
        return self._store.count_recent_attempts_for_chat(
            chat_id=chat_id,
            window_seconds=self._config.attempt_window_seconds,
        )

    def record_pair_attempt(
        self,
        *,
        channel: str,
        account_id: str,
        chat_key: str | None,
        token: str,
        outcome: str,
        hash_pepper: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        user_id = _telegram_id_suffix(account_id)
        chat_id = _telegram_id_suffix(chat_key)
        if user_id is None or chat_id is None:
            return
        self._store.record_pair_attempt(
            token=token,
            user_id=user_id,
            chat_id=chat_id,
            outcome=outcome,
            hash_pepper=hash_pepper,
        )

    def has_pair_channel_data(self, *, channel: str) -> bool:
        return any(True for _ in self._store.iter_pair_tokens())

    def bulk_insert_pair_tokens(self, rows: Any) -> int:
        return 0

    def bulk_insert_pair_attempts(self, rows: Any) -> int:
        return 0


class TelegramPairingService:
    contract_version = TELEGRAM_INTERFACE_VERSION

    def __init__(
        self,
        *,
        config: PairingConfig,
        store: TelegramPollStateStore,
        controlplane_store: _ControlPlanePairingStore | None = None,
        logger: logging.Logger | None = None,
        lru: RecentPairAttemptsLRU | None = None,
        audit_logger: object | None = None,
    ) -> None:
        warnings.warn(
            "TelegramPairingService is a compatibility wrapper; use "
            "ControlPlanePairingService with TelegramPairingAdapter for new code.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._config = config
        self._store = store
        self._controlplane_store = controlplane_store
        self._log = logger or logging.getLogger(__name__)
        self._lru = lru or RecentPairAttemptsLRU()
        self._adapter = TelegramPairingAdapter()
        self._pairing_store = ControlPlanePairingStore(
            cast(Any, controlplane_store)
            if _has_controlplane_pairing_methods(controlplane_store)
            else _LegacyTelegramPairingStoreAdapter(store=store, config=config)
        )
        self._service = ControlPlanePairingService(
            policy=PairingPolicy.from_config(config),
            store=self._pairing_store,
            adapter=self._adapter,
            bridge_store=cast(Any, controlplane_store),
            legacy_store=store if controlplane_store is not None else None,
            audit_logger=audit_logger,
            logger=self._log,
            lru=self._lru,
        )

    def issue_token(
        self,
        *,
        expected_user_id: int | None,
        expected_chat_id: int | None,
        token_ttl_seconds: int | None = None,
        scopes: list[str] | None = None,
        token: str | None = None,
    ) -> PairCreateResult:
        issued = self._service.issue_token(
            expected_account_id=_telegram_account_id(expected_user_id),
            expected_chat_key=_telegram_chat_key(expected_chat_id),
            token_ttl_seconds=token_ttl_seconds or self._config.token_ttl_seconds,
            scopes=scopes or list(self._config.default_scopes),
            token=token,
        )
        if _has_controlplane_pairing_methods(self._controlplane_store):
            self._dual_write_legacy_issue(
                expected_user_id=expected_user_id,
                expected_chat_id=expected_chat_id,
                issued=issued,
            )
        return issued

    def handle_start_pairing(
        self,
        envelope: TelegramInboundEnvelope,
        *,
        bot_username: str | None,
    ) -> PairingHandleResult:
        control_event = to_control_event(envelope)
        inbound = to_inbound_message(
            envelope,
            normalized_text=envelope.text,
            control_event=control_event,
        )
        return self._service.handle_pairing_attempt(
            inbound,
            channel_context={"bot_username": bot_username},
        )

    def _dual_write_legacy_issue(
        self,
        *,
        expected_user_id: int | None,
        expected_chat_id: int | None,
        issued: PairCreateResult,
    ) -> None:
        try:
            self._store.issue_pair_token(
                token=issued.token,
                token_ttl_seconds=max(60, issued.expires_at_ts - _now_ts()),
                scopes=list(issued.scopes or self._config.default_scopes),
                expected_user_id=expected_user_id,
                expected_chat_id=expected_chat_id,
                hash_pepper=self._config.hash_pepper,
            )
        except (TypeError, ValueError, RuntimeError, sqlite3.Error) as exc:
            self._log.warning("telegram pairing legacy dual-write failed: %s", exc)


def _issue_to_dict(issued: PairTokenIssue) -> dict[str, Any]:
    return {
        "token": issued.token,
        "token_hint": issued.token_hint,
        "token_hash_prefix": issued.token_hash_prefix,
        "expires_at_ts": issued.expires_at_ts,
        "scopes": list(issued.scopes or []),
    }


def _has_controlplane_pairing_methods(store: object | None) -> bool:
    return store is not None and all(
        hasattr(store, name)
        for name in (
            "issue_pair_token",
            "consume_pair_token",
            "record_pair_attempt",
            "has_pair_channel_data",
        )
    )


def _telegram_account_id(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    return f"telegram-bot:user:{int(user_id)}"


def _telegram_chat_key(chat_id: int | None) -> str | None:
    if chat_id is None:
        return None
    return f"telegram-bot:chat:{int(chat_id)}"


def _telegram_id_suffix(value: object | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text.rsplit(":", 1)[-1])
    except ValueError:
        return None


def _now_ts() -> int:
    from openminion.modules.controlplane.pairing.store import now_ts

    return now_ts()


def _extract_start_token(text: str, *, bot_username: str | None) -> str | None:
    from openminion.modules.controlplane.channels.telegram.pairing_text import (
        extract_start_token,
    )

    return extract_start_token(text, bot_username=bot_username)


__all__ = [
    "PairCreateResult",
    "PairingHandleResult",
    "RecentPairAttemptsLRU",
    "TelegramPairingService",
    "_extract_start_token",
]
