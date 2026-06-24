import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol

from openminion.modules.controlplane.constants import PRINCIPAL_BINDING_STATUS_ACTIVE
from openminion.modules.controlplane.channels.telegram.config import PairingConfig
from openminion.modules.controlplane.channels.telegram.constants import PAIRING_MODE_OFF
from openminion.modules.controlplane.channels.telegram.interfaces import (
    TELEGRAM_INTERFACE_VERSION,
)
from openminion.modules.controlplane.channels.telegram.models import (
    PairingHandleResult,
    TelegramInboundEnvelope,
)
from openminion.modules.controlplane.channels.telegram.state import (
    TelegramPollStateStore,
)

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class PairCreateResult:
    token: str
    token_hint: str
    token_hash_prefix: str
    expires_at_ts: int
    scopes: list[str]


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


class _ControlPlaneSessionResolver(Protocol):
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
    ) -> None:
        self._config = config
        self._store = store
        self._controlplane_store = controlplane_store
        self._log = logger or logging.getLogger(__name__)
        self._lru = lru or RecentPairAttemptsLRU()

    def issue_token(
        self,
        *,
        expected_user_id: int | None,
        expected_chat_id: int | None,
        token_ttl_seconds: int | None = None,
        scopes: list[str] | None = None,
        token: str | None = None,
    ) -> PairCreateResult:
        ttl = token_ttl_seconds or self._config.token_ttl_seconds
        scoped = scopes or list(self._config.default_scopes)
        issued = self._store.issue_pair_token(
            token=token,
            token_ttl_seconds=ttl,
            scopes=scoped,
            expected_user_id=expected_user_id,
            expected_chat_id=expected_chat_id,
            hash_pepper=self._config.hash_pepper,
        )
        return PairCreateResult(
            token=issued.token,
            token_hint=issued.token_hint,
            token_hash_prefix=issued.token_hash_prefix,
            expires_at_ts=issued.expires_at_ts,
            scopes=issued.scopes,
        )

    def handle_start_pairing(
        self,
        envelope: TelegramInboundEnvelope,
        *,
        bot_username: str | None,
    ) -> PairingHandleResult:
        if not self._config.enabled or self._config.mode == PAIRING_MODE_OFF:
            return PairingHandleResult(handled=False)

        token = _extract_start_token(envelope.text, bot_username=bot_username)
        if token is None:
            return PairingHandleResult(handled=False)

        if envelope.chat_type != "private" and not self._config.allow_in_groups:
            return PairingHandleResult(
                handled=True,
                reply_text="Pairing is only allowed in direct messages.",
            )

        if not _TOKEN_RE.fullmatch(token):
            self._record_attempt(
                token=token, envelope=envelope, outcome="invalid_format"
            )
            return PairingHandleResult(
                handled=True,
                reply_text="Pairing failed or expired. Generate a new link.",
            )

        lru_count = self._lru.bump(token[:12])
        if lru_count > 8:
            self._record_attempt(token=token, envelope=envelope, outcome="lru_limited")
            return PairingHandleResult(
                handled=True,
                reply_text="Too many pairing attempts. Try again shortly.",
            )

        if self._is_rate_limited(envelope):
            self._record_attempt(token=token, envelope=envelope, outcome="rate_limited")
            return PairingHandleResult(
                handled=True,
                reply_text="Too many pairing attempts. Try again shortly.",
            )

        consume = self._store.consume_pair_token(
            token=token,
            user_id=envelope.from_user.id,
            chat_id=envelope.chat_id,
            topic_id=envelope.topic_id,
            hash_pepper=self._config.hash_pepper,
        )
        self._record_attempt(token=token, envelope=envelope, outcome=consume.reason)

        if consume.ok:
            self._bridge_pairing_to_controlplane(envelope=envelope, consume=consume)
            self._log.info(
                "telegram pairing success user=%s chat=%s hint=%s hash_prefix=%s",
                envelope.from_user.id,
                envelope.chat_id,
                consume.token_hint,
                consume.token_hash_prefix,
            )
            return PairingHandleResult(handled=True, reply_text="Paired ✅")

        self._log.warning(
            "telegram pairing denied user=%s chat=%s reason=%s hint=%s hash_prefix=%s",
            envelope.from_user.id,
            envelope.chat_id,
            consume.reason,
            consume.token_hint,
            consume.token_hash_prefix,
        )
        return PairingHandleResult(
            handled=True,
            reply_text="Pairing failed or expired. Generate a new link.",
        )

    def _is_rate_limited(self, envelope: TelegramInboundEnvelope) -> bool:
        return (
            self._store.count_recent_attempts_for_user(
                user_id=envelope.from_user.id,
                window_seconds=self._config.attempt_window_seconds,
            )
            >= self._config.max_attempts_per_user
            or self._store.count_recent_attempts_for_chat(
                chat_id=envelope.chat_id,
                window_seconds=self._config.attempt_window_seconds,
            )
            >= self._config.max_attempts_per_chat
        )

    def _record_attempt(
        self, *, token: str, envelope: TelegramInboundEnvelope, outcome: str
    ) -> None:
        self._store.record_pair_attempt(
            token=token,
            user_id=envelope.from_user.id,
            chat_id=envelope.chat_id,
            outcome=outcome,
            hash_pepper=self._config.hash_pepper,
        )

    def _bridge_pairing_to_controlplane(
        self,
        *,
        envelope: TelegramInboundEnvelope,
        consume: Any,
    ) -> None:
        bridge = self._controlplane_store
        if bridge is None:
            return

        chat_id = str(envelope.chat_id)
        user_id = str(envelope.from_user.id)
        user_key = f"telegram:{envelope.from_user.id}"
        chat_key = (
            f"telegram:{envelope.chat_id}:{envelope.topic_id}"
            if envelope.topic_id is not None
            else f"telegram:{envelope.chat_id}"
        )
        session_id = f"telegram-pair:{envelope.chat_id}"

        if hasattr(bridge, "resolve_session"):
            try:
                resolver = bridge  # type: ignore[assignment]
                session_id = str(
                    _as_session_resolver(resolver).resolve_session(
                        user_key=user_key,
                        chat_key=chat_key,
                    )
                )
            except Exception as exc:
                self._log.warning(
                    "telegram pairing bridge resolve_session failed: %s", exc
                )

        scopes = list(consume.scopes or self._config.default_scopes)
        try:
            bridge.upsert_pairing(
                channel="telegram",
                chat_id=chat_id,
                user_id=user_id,
                session_id=session_id,
                scopes=scopes,
                note="telegram_pair_bridge",
            )
        except Exception as exc:
            self._log.warning(
                "telegram pairing bridge upsert failed user=%s chat=%s: %s",
                user_id,
                chat_id,
                exc,
            )


def _as_session_resolver(store: object) -> _ControlPlaneSessionResolver:
    return store  # type: ignore[return-value]


def _extract_start_token(text: str, *, bot_username: str | None) -> str | None:
    stripped = (text or "").strip()
    if not stripped:
        return None

    parts = stripped.split(maxsplit=1)
    head = parts[0]
    if not head.startswith("/"):
        return None

    cmd = head[1:]
    if "@" in cmd:
        name, target_bot = cmd.split("@", 1)
        if bot_username and target_bot.lower() != bot_username.lower():
            return None
        cmd = name

    if cmd.lower() != "start":
        return None
    if len(parts) < 2:
        return None

    token = parts[1].strip().split()[0]
    return token or None
