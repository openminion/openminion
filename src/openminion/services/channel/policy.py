from dataclasses import dataclass
from typing import Iterable

from openminion.base.config import ChannelPolicyConfig

DM_POLICY_VALUES = {"pairing", "allowlist", "open", "disabled"}
GROUP_POLICY_VALUES = {"allowlist", "open", "disabled"}


@dataclass(frozen=True)
class ChannelPolicyDecision:
    allowed: bool
    reason: str
    policy: str
    requires_pairing: bool = False


@dataclass(frozen=True)
class ChannelAccessPolicy:
    dm_policy: str
    group_policy: str
    dm_allowlist: tuple[str, ...] = ()
    group_allowlist: tuple[str, ...] = ()
    paired_dm_senders: tuple[str, ...] = ()


def build_channel_access_policy(config: ChannelPolicyConfig) -> ChannelAccessPolicy:
    dm_policy = _normalize_policy(
        raw=config.dm_policy,
        allowed=DM_POLICY_VALUES,
        default="pairing",
    )
    group_policy = _normalize_policy(
        raw=config.group_policy,
        allowed=GROUP_POLICY_VALUES,
        default="disabled",
    )
    return ChannelAccessPolicy(
        dm_policy=dm_policy,
        group_policy=group_policy,
        dm_allowlist=_normalize_id_set(config.dm_allowlist),
        group_allowlist=_normalize_id_set(config.group_allowlist),
        paired_dm_senders=_normalize_id_set(config.paired_dm_senders),
    )


def evaluate_inbound_policy(
    *,
    policy: ChannelAccessPolicy,
    sender_id: str,
    is_group: bool,
    group_id: str | None = None,
) -> ChannelPolicyDecision:
    normalized_sender = _normalize_id(sender_id)
    if not normalized_sender:
        return ChannelPolicyDecision(
            allowed=False,
            reason="sender_id is required for inbound policy checks.",
            policy="invalid",
        )

    if is_group:
        normalized_group = _normalize_id(group_id or "")
        return _evaluate_group(
            policy=policy, sender_id=normalized_sender, group_id=normalized_group
        )
    return _evaluate_dm(policy=policy, sender_id=normalized_sender)


def _evaluate_dm(
    *, policy: ChannelAccessPolicy, sender_id: str
) -> ChannelPolicyDecision:
    if policy.dm_policy == "open":
        return ChannelPolicyDecision(
            allowed=True, reason="DM policy is open.", policy=policy.dm_policy
        )
    if policy.dm_policy == "disabled":
        return ChannelPolicyDecision(
            allowed=False, reason="DM inbound is disabled.", policy=policy.dm_policy
        )
    if policy.dm_policy == "allowlist":
        if sender_id in policy.dm_allowlist:
            return ChannelPolicyDecision(
                allowed=True,
                reason="Sender is in DM allowlist.",
                policy=policy.dm_policy,
            )
        return ChannelPolicyDecision(
            allowed=False,
            reason="Sender is not in DM allowlist.",
            policy=policy.dm_policy,
        )
    if sender_id in policy.paired_dm_senders:
        return ChannelPolicyDecision(
            allowed=True,
            reason="Sender is paired for DM policy.",
            policy=policy.dm_policy,
        )
    return ChannelPolicyDecision(
        allowed=False,
        reason="Sender is not paired for DM policy.",
        policy=policy.dm_policy,
        requires_pairing=True,
    )


def _evaluate_group(
    *, policy: ChannelAccessPolicy, sender_id: str, group_id: str
) -> ChannelPolicyDecision:
    del sender_id
    if policy.group_policy == "open":
        return ChannelPolicyDecision(
            allowed=True,
            reason="Group policy is open.",
            policy=policy.group_policy,
        )
    if policy.group_policy == "disabled":
        return ChannelPolicyDecision(
            allowed=False,
            reason="Group inbound is disabled.",
            policy=policy.group_policy,
        )
    if group_id and group_id in policy.group_allowlist:
        return ChannelPolicyDecision(
            allowed=True,
            reason="Group is in allowlist.",
            policy=policy.group_policy,
        )
    return ChannelPolicyDecision(
        allowed=False,
        reason="Group is not in allowlist.",
        policy=policy.group_policy,
    )


def _normalize_policy(*, raw: str, allowed: set[str], default: str) -> str:
    candidate = str(raw).strip().lower()
    if candidate in allowed:
        return candidate
    return default


def _normalize_id(value: str) -> str:
    return str(value).strip().lower()


def _normalize_id_set(values: Iterable[str]) -> tuple[str, ...]:
    normalized = {_normalize_id(value) for value in values if _normalize_id(value)}
    return tuple(sorted(normalized))
