from __future__ import annotations

from typing import Any

from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.pairing.adapter import PairingAttempt

from .normalization import session_scope_key
from .pairing_text import extract_start_token as _extract_start_token


class TelegramPairingAdapter:
    @property
    def channel_id(self) -> str:
        return "telegram"

    @property
    def account_namespace(self) -> str:
        return "telegram-bot"

    def extract_pairing_attempt(
        self,
        inbound: InboundMessage,
        *,
        channel_context: dict[str, Any] | None = None,
    ) -> PairingAttempt | None:
        context = dict(channel_context or {})
        token = _extract_start_token(
            inbound.text,
            bot_username=context.get("bot_username"),
        )
        if token is None:
            return None

        telegram = dict(inbound.metadata.get("telegram") or {})
        user_id = telegram.get("from_user_id") or inbound.user_id
        chat_id = telegram.get("chat_id") or inbound.chat_id
        if user_id is None or chat_id is None:
            return None
        topic_id = telegram.get("topic_id")
        chat_type = str(telegram.get("chat_type") or "private")
        session_chat_key = session_scope_key(int(chat_id), _optional_int(topic_id))
        return PairingAttempt(
            channel="telegram",
            token=token,
            account_id=f"{self.account_namespace}:user:{user_id}",
            chat_key=f"{self.account_namespace}:chat:{chat_id}",
            chat_type=chat_type,
            extra={
                "topic_id": topic_id,
                "telegram_user_id": user_id,
                "telegram_chat_id": chat_id,
                "subject_id": str(chat_id),
                "user_id": str(user_id),
                "session_user_key": f"telegram:{user_id}",
                "session_chat_key": session_chat_key,
            },
        )

    def format_pairing_hint(self, token: str, *, ttl_seconds: int) -> str:
        minutes = max(1, int(ttl_seconds) // 60)
        return f"Send /start {token} to the bot within {minutes} minutes."

    def format_success_reply(self) -> str:
        return "Paired ✅"

    def format_failure_reply(self, reason: str) -> str:
        if reason in {"lru_limited", "rate_limited"}:
            return "Too many pairing attempts. Try again shortly."
        return "Pairing failed or expired. Generate a new link."


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
