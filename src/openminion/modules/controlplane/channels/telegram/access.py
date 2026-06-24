from openminion.modules.controlplane.interfaces import CONTROLPLANE_INTERFACE_VERSION
from openminion.modules.controlplane.channels.telegram.config import AccessConfig
from openminion.modules.controlplane.channels.telegram.constants import (
    ACCESS_REASON_DM_ALLOWLIST_MISS,
    ACCESS_REASON_DM_POLICY_DENY,
    ACCESS_REASON_GROUP_ALLOWLIST_MISS,
    ACCESS_REASON_GROUP_POLICY_DENY,
    ACCESS_REASON_MENTION_REQUIRED,
    ACCESS_REASON_OK,
    ACCESS_REASON_TOPIC_NOT_ALLOWED,
    ACCESS_REASON_TOPIC_REQUIRED,
)
from openminion.modules.controlplane.channels.telegram.models import (
    AccessDecision,
    TelegramInboundEnvelope,
)


class TelegramAccessPolicy:
    """Pre-auth network access policy for Telegram inbound envelopes."""

    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def __init__(self, *, access: AccessConfig) -> None:
        self._access = access

    def evaluate(
        self,
        envelope: TelegramInboundEnvelope,
        *,
        bot_username: str | None,
    ) -> AccessDecision:
        command_mode = _is_command(envelope.text)

        if envelope.chat_type == "private":
            return _evaluate_dm(envelope, self._access)

        if envelope.is_group:
            policy = _evaluate_group_policy(envelope, self._access)
            if not policy.allowed:
                return policy

            topic_gate = _evaluate_topic_gate(envelope, self._access)
            if not topic_gate.allowed:
                return topic_gate

            if self._access.mention_only_in_groups and not command_mode:
                if not _mentions_bot(envelope.text, bot_username):
                    return AccessDecision(False, ACCESS_REASON_MENTION_REQUIRED)

            return AccessDecision(True, ACCESS_REASON_OK)

        return AccessDecision(True, ACCESS_REASON_OK)


def _evaluate_dm(
    envelope: TelegramInboundEnvelope, access: AccessConfig
) -> AccessDecision:
    if access.dm_policy == "deny":
        return AccessDecision(False, ACCESS_REASON_DM_POLICY_DENY)
    if access.dm_policy == "allow":
        return AccessDecision(True, ACCESS_REASON_OK)
    if envelope.from_user.id in set(access.allow_from_user_ids):
        return AccessDecision(True, ACCESS_REASON_OK)
    return AccessDecision(False, ACCESS_REASON_DM_ALLOWLIST_MISS)


def _evaluate_group_policy(
    envelope: TelegramInboundEnvelope, access: AccessConfig
) -> AccessDecision:
    if access.group_policy == "deny":
        return AccessDecision(False, ACCESS_REASON_GROUP_POLICY_DENY)
    if access.group_policy == "allow":
        return AccessDecision(True, ACCESS_REASON_OK)
    if envelope.chat_id in set(access.allow_group_chat_ids):
        return AccessDecision(True, ACCESS_REASON_OK)
    return AccessDecision(False, ACCESS_REASON_GROUP_ALLOWLIST_MISS)


def _evaluate_topic_gate(
    envelope: TelegramInboundEnvelope, access: AccessConfig
) -> AccessDecision:
    allowed_topics = access.allowed_topic_ids_by_chat.get(str(envelope.chat_id))
    if not allowed_topics:
        return AccessDecision(True, ACCESS_REASON_OK)
    if envelope.topic_id is None:
        return AccessDecision(False, ACCESS_REASON_TOPIC_REQUIRED)
    if envelope.topic_id in set(allowed_topics):
        return AccessDecision(True, ACCESS_REASON_OK)
    return AccessDecision(False, ACCESS_REASON_TOPIC_NOT_ALLOWED)


def _is_command(text: str) -> bool:
    return (text or "").strip().startswith("/")


def _mentions_bot(text: str, bot_username: str | None) -> bool:
    if not bot_username:
        return False
    token = f"@{bot_username}".lower()
    return token in (text or "").lower()
