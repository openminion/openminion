from __future__ import annotations

from openminion.modules.controlplane.interfaces import (
    ensure_controlplane_component_compatibility,
)
from openminion.modules.controlplane.channels.telegram.access import (
    TelegramAccessPolicy,
)
from openminion.modules.controlplane.channels.telegram.config import AccessConfig
from openminion.modules.controlplane.channels.telegram.models import (
    TelegramInboundEnvelope,
    TelegramUser,
)


def _envelope(
    *,
    chat_type: str,
    chat_id: int,
    user_id: int,
    text: str,
    topic_id: int | None = None,
) -> TelegramInboundEnvelope:
    return TelegramInboundEnvelope(
        update_id=1,
        raw_type="message",
        chat_id=chat_id,
        message_id=10,
        text=text,
        from_user=TelegramUser(id=user_id, username="u", display="U"),
        chat_type=chat_type,
        topic_id=topic_id,
        raw_update={},
    )


def test_dm_allowlist_blocks_unknown_user() -> None:
    policy = TelegramAccessPolicy(
        access=AccessConfig(dm_policy="allowlist", allow_from_user_ids=[42])
    )
    decision = policy.evaluate(
        _envelope(chat_type="private", chat_id=5, user_id=99, text="hi"),
        bot_username="bot",
    )
    assert decision.allowed is False
    assert decision.reason == "dm_allowlist_miss"


def test_telegram_access_policy_satisfies_controlplane_contract() -> None:
    policy = TelegramAccessPolicy(access=AccessConfig(group_policy="allow"))
    ensure_controlplane_component_compatibility(policy, component_type="access_policy")


def test_group_mention_only_allows_commands_without_mention() -> None:
    policy = TelegramAccessPolicy(
        access=AccessConfig(group_policy="allow", mention_only_in_groups=True)
    )
    decision = policy.evaluate(
        _envelope(chat_type="supergroup", chat_id=-1001, user_id=99, text="/help"),
        bot_username="mybot",
    )
    assert decision.allowed is True


def test_group_mention_only_blocks_non_command_without_mention() -> None:
    policy = TelegramAccessPolicy(
        access=AccessConfig(group_policy="allow", mention_only_in_groups=True)
    )
    decision = policy.evaluate(
        _envelope(chat_type="group", chat_id=-1, user_id=99, text="hello there"),
        bot_username="mybot",
    )
    assert decision.allowed is False
    assert decision.reason == "mention_required"


def test_topic_allowlist_blocks_wrong_topic() -> None:
    policy = TelegramAccessPolicy(
        access=AccessConfig(
            group_policy="allow",
            allowed_topic_ids_by_chat={"-1001": [42, 43]},
        )
    )
    decision = policy.evaluate(
        _envelope(
            chat_type="supergroup",
            chat_id=-1001,
            user_id=99,
            text="/status",
            topic_id=77,
        ),
        bot_username="mybot",
    )
    assert decision.allowed is False
    assert decision.reason == "topic_not_allowed"
