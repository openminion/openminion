from copy import deepcopy
from typing import Any

from .mapping import mapping_payload


def _normalize_channel_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    raw_enabled_channels = payload.get("enabled_channels")
    enabled_channels = (
        raw_enabled_channels if isinstance(raw_enabled_channels, list) else ["console"]
    )

    channels_payload = payload.get("channels")
    channels: dict[str, dict[str, Any]] = {}
    if isinstance(channels_payload, dict):
        for raw_name, raw_cfg in channels_payload.items():
            name = str(raw_name).strip()
            if not name or not isinstance(raw_cfg, dict):
                continue
            channels[name] = deepcopy(raw_cfg)

    raw_enabled_plugins = payload.get("enabled_plugins")
    enabled_plugins = (
        raw_enabled_plugins if isinstance(raw_enabled_plugins, list) else ["validate"]
    )

    channel_policy_payload = mapping_payload(payload, "channel_policy")
    channel_authenticity_payload = mapping_payload(payload, "channel_authenticity")

    dm_allowlist = channel_policy_payload.get("dm_allowlist")
    group_allowlist = channel_policy_payload.get("group_allowlist")
    paired_dm_senders = channel_policy_payload.get("paired_dm_senders")
    trusted_channels = channel_authenticity_payload.get("trusted_channels")
    required_channels = channel_authenticity_payload.get("required_channels")
    dm_allowlist = dm_allowlist if isinstance(dm_allowlist, list) else []
    group_allowlist = group_allowlist if isinstance(group_allowlist, list) else []
    paired_dm_senders = paired_dm_senders if isinstance(paired_dm_senders, list) else []
    trusted_channels = (
        trusted_channels if isinstance(trusted_channels, list) else ["console"]
    )
    required_channels = required_channels if isinstance(required_channels, list) else []

    return {
        "enabled_channels": [str(item) for item in enabled_channels],
        "channels": channels,
        "enabled_plugins": [str(item) for item in enabled_plugins],
        "dm_allowlist": dm_allowlist,
        "group_allowlist": group_allowlist,
        "paired_dm_senders": paired_dm_senders,
        "trusted_channels": trusted_channels,
        "required_channels": required_channels,
    }
