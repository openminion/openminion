"""Slack pairing adapter for the shared controlplane pairing service."""

from __future__ import annotations

from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.pairing.adapter import PairingAttempt

from .constants import CHANNEL_ID
from .normalization import slack_session_scope_key


class SlackPairingAdapter:
    @property
    def channel_id(self) -> str:
        return CHANNEL_ID

    @property
    def account_namespace(self) -> str:
        return "slack"

    def extract_pairing_attempt(
        self,
        inbound: InboundMessage,
        *,
        channel_context: dict[str, object] | None = None,
    ) -> PairingAttempt | None:
        token = _extract_pair_token(inbound.text)
        if token is None:
            return None

        metadata = dict(inbound.metadata or inbound.meta or {})
        team_id = str(metadata.get("team_id") or "").strip()
        channel_id = str(metadata.get("channel_id") or inbound.chat_id or "").strip()
        user_id = str(inbound.user_id or "").strip()
        if not team_id or not channel_id or not user_id:
            return None

        chat_type = (
            "private"
            if metadata.get("channel_type") == "im"
            or metadata.get("slack_interaction") == "slash_command"
            else "channel"
        )
        session_chat_key = slack_session_scope_key(
            team_id,
            channel_id,
            str(inbound.thread_id or "").strip() or None,
        )
        pair_chat_key = slack_session_scope_key(team_id, channel_id, None)
        return PairingAttempt(
            channel=CHANNEL_ID,
            token=token,
            account_id=f"{self.account_namespace}:{team_id}:user:{user_id}",
            chat_key=pair_chat_key,
            chat_type=chat_type,
            extra={
                "team_id": team_id,
                "channel_id": channel_id,
                "subject_id": channel_id,
                "user_id": user_id,
                "session_user_key": f"slack:{team_id}:user:{user_id}",
                "session_chat_key": session_chat_key,
            },
        )

    def format_pairing_hint(self, token: str, *, ttl_seconds: int) -> str:
        minutes = max(1, int(ttl_seconds) // 60)
        return f"Send /openminion pair {token} within {minutes} minutes."

    def format_success_reply(self) -> str:
        return "Paired."

    def format_failure_reply(self, reason: str) -> str:
        if reason in {"lru_limited", "rate_limited"}:
            return "Too many pairing attempts. Try again shortly."
        return "Pairing failed or expired. Generate a new token."


def _extract_pair_token(text: str) -> str | None:
    parts = str(text or "").strip().split()
    if len(parts) == 2 and parts[0].lower() in {"/pair", "pair"}:
        return parts[1]
    if len(parts) == 3 and parts[0].lower() == "/openminion" and parts[1] == "pair":
        return parts[2]
    return None


__all__ = ["SlackPairingAdapter"]
