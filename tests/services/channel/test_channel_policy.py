from openminion.base.config import ChannelPolicyConfig
from openminion.modules.controlplane.channels.policy import (
    build_channel_access_policy,
    evaluate_inbound_policy,
)


def test_build_policy_normalizes_values_and_lists() -> None:
    config = ChannelPolicyConfig(
        dm_policy="ALLOWLIST",
        group_policy="invalid",
        dm_allowlist=["User-1", " user-1 ", "USER-2"],
        group_allowlist=["Group-A", "group-a", "group-b"],
        paired_dm_senders=["Paired-1", "PAIRED-1"],
    )
    policy = build_channel_access_policy(config)
    assert policy.dm_policy == "allowlist"
    assert policy.group_policy == "disabled"
    assert policy.dm_allowlist == ("user-1", "user-2")
    assert policy.group_allowlist == ("group-a", "group-b")
    assert policy.paired_dm_senders == ("paired-1",)


def test_dm_pairing_requires_paired_sender() -> None:
    policy = build_channel_access_policy(
        ChannelPolicyConfig(dm_policy="pairing", paired_dm_senders=["sender-1"])
    )
    denied = evaluate_inbound_policy(
        policy=policy,
        sender_id="sender-2",
        is_group=False,
    )
    assert not denied.allowed
    assert denied.requires_pairing

    allowed = evaluate_inbound_policy(
        policy=policy,
        sender_id="sender-1",
        is_group=False,
    )
    assert allowed.allowed
    assert not allowed.requires_pairing


def test_dm_allowlist_blocks_unknown_sender() -> None:
    policy = build_channel_access_policy(
        ChannelPolicyConfig(dm_policy="allowlist", dm_allowlist=["alice"])
    )
    allowed = evaluate_inbound_policy(policy=policy, sender_id="alice", is_group=False)
    denied = evaluate_inbound_policy(policy=policy, sender_id="bob", is_group=False)
    assert allowed.allowed
    assert not denied.allowed


def test_group_allowlist_checks_group_id() -> None:
    policy = build_channel_access_policy(
        ChannelPolicyConfig(group_policy="allowlist", group_allowlist=["team-room"])
    )
    allowed = evaluate_inbound_policy(
        policy=policy,
        sender_id="alice",
        is_group=True,
        group_id="team-room",
    )
    denied = evaluate_inbound_policy(
        policy=policy,
        sender_id="alice",
        is_group=True,
        group_id="other-room",
    )
    assert allowed.allowed
    assert not denied.allowed


def test_group_open_allows_and_group_disabled_denies() -> None:
    open_policy = build_channel_access_policy(ChannelPolicyConfig(group_policy="open"))
    disabled_policy = build_channel_access_policy(
        ChannelPolicyConfig(group_policy="disabled")
    )

    open_decision = evaluate_inbound_policy(
        policy=open_policy,
        sender_id="alice",
        is_group=True,
        group_id="room-1",
    )
    denied_decision = evaluate_inbound_policy(
        policy=disabled_policy,
        sender_id="alice",
        is_group=True,
        group_id="room-1",
    )
    assert open_decision.allowed
    assert not denied_decision.allowed


def test_missing_sender_is_denied() -> None:
    policy = build_channel_access_policy(ChannelPolicyConfig())
    decision = evaluate_inbound_policy(policy=policy, sender_id=" ", is_group=False)
    assert not decision.allowed
    assert decision.policy == "invalid"
