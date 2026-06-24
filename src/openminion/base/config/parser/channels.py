from __future__ import annotations

from copy import deepcopy
from typing import Any

from .mapping import mapping_payload


def _normalize_channel_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    enabled_channels = payload.get("enabled_channels")
    if not isinstance(enabled_channels, list):
        enabled_channels = ["console"]

    channels_payload = payload.get("channels")
    channels: dict[str, dict[str, Any]] = {}
    if isinstance(channels_payload, dict):
        for raw_name, raw_cfg in channels_payload.items():
            name = str(raw_name).strip()
            if not name or not isinstance(raw_cfg, dict):
                continue
            channels[name] = deepcopy(raw_cfg)

    enabled_plugins = payload.get("enabled_plugins")
    if not isinstance(enabled_plugins, list):
        enabled_plugins = ["validate"]

    channel_policy_payload = mapping_payload(payload, "channel_policy")
    channel_authenticity_payload = mapping_payload(payload, "channel_authenticity")

    dm_allowlist = channel_policy_payload.get("dm_allowlist")
    if not isinstance(dm_allowlist, list):
        dm_allowlist = []

    group_allowlist = channel_policy_payload.get("group_allowlist")
    if not isinstance(group_allowlist, list):
        group_allowlist = []

    paired_dm_senders = channel_policy_payload.get("paired_dm_senders")
    if not isinstance(paired_dm_senders, list):
        paired_dm_senders = []

    trusted_channels = channel_authenticity_payload.get("trusted_channels")
    if not isinstance(trusted_channels, list):
        trusted_channels = ["console"]

    required_channels = channel_authenticity_payload.get("required_channels")
    if not isinstance(required_channels, list):
        required_channels = []

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
