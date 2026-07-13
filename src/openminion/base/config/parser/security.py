"""Gateway, channel policy, and security parsing helpers."""

from __future__ import annotations

from typing import Any

from openminion.base.config.core import (
    ChannelAuthenticityConfig,
    ChannelPolicyConfig,
    GatewayConfig,
    OpenMinionConfig,
    SecurityConfig,
)
from openminion.base.config.parse import (
    _as_int,
    _as_string_dict,
    _normalize_brain_integration_mode,
    _normalize_channel_authenticity_mode,
)
from openminion.base.config.runtime import ToolPolicyConfig

from .action import (
    _action_policy_to_payload,
    _build_action_policy_config,
)


def _list_payload(payload: dict[str, Any], key: str, default: list[Any]) -> list[Any]:
    value = payload.get(key)
    return value if isinstance(value, list) else list(default)


def _string_ids(values: list[Any]) -> list[str]:
    return [str(item) for item in values if isinstance(item, (str, int))]


def _normalized_tokens(
    values: list[Any], default: list[str] | None = None
) -> list[str]:
    tokens = [str(item).strip().lower() for item in values if str(item).strip()]
    return tokens or list(default or [])


def _build_gateway_security_sections(
    *,
    gateway_payload: dict[str, Any],
    channel_policy_payload: dict[str, Any],
    channel_authenticity_payload: dict[str, Any],
    security_payload: dict[str, Any],
    action_policy_payload: dict[str, Any],
    normalized_channel_defaults: dict[str, Any],
) -> dict[str, Any]:
    raw_tool_policy = security_payload.get("tool_policy")
    tool_policy_payload = raw_tool_policy if isinstance(raw_tool_policy, dict) else {}
    return {
        "gateway": GatewayConfig(
            host=str(gateway_payload.get("host", "127.0.0.1")),
            port=_as_int(gateway_payload.get("port"), 18789),
            api_turn_timeout_seconds=_as_int(
                gateway_payload.get("api_turn_timeout_seconds"), 45
            ),
            brain_integration_mode=_normalize_brain_integration_mode(
                gateway_payload.get("brain_integration_mode")
            ),
        ),
        "channel_policy": ChannelPolicyConfig(
            dm_policy=str(channel_policy_payload.get("dm_policy", "pairing")),
            group_policy=str(channel_policy_payload.get("group_policy", "disabled")),
            dm_allowlist=_string_ids(normalized_channel_defaults["dm_allowlist"]),
            group_allowlist=_string_ids(normalized_channel_defaults["group_allowlist"]),
            paired_dm_senders=_string_ids(
                normalized_channel_defaults["paired_dm_senders"]
            ),
        ),
        "channel_authenticity": ChannelAuthenticityConfig(
            mode=_normalize_channel_authenticity_mode(
                channel_authenticity_payload.get("mode")
            ),
            trusted_channels=_normalized_tokens(
                normalized_channel_defaults["trusted_channels"], ["console"]
            ),
            required_channels=_normalized_tokens(
                normalized_channel_defaults["required_channels"]
            ),
            secret_env_by_channel=_as_string_dict(
                channel_authenticity_payload.get("secret_env_by_channel"),
                lower_keys=True,
            ),
            max_age_seconds=max(
                0,
                _as_int(channel_authenticity_payload.get("max_age_seconds"), 300),
            ),
            allowed_algorithms=_normalized_tokens(
                _list_payload(
                    channel_authenticity_payload,
                    "allowed_algorithms",
                    ["hmac-sha256"],
                ),
                ["hmac-sha256"],
            ),
        ),
        "security": SecurityConfig(
            tool_policy=ToolPolicyConfig(
                default_required_scopes=_normalized_tokens(
                    _list_payload(
                        tool_policy_payload,
                        "default_required_scopes",
                        ["tool.execute"],
                    ),
                    ["tool.execute"],
                ),
                **{
                    name: max(1, _as_int(tool_policy_payload.get(name), default))
                    for name, default in (
                        ("max_calls_per_run", 8),
                        ("max_calls_per_tool", 4),
                        ("max_budget_cost_per_run", 16),
                    )
                },
            )
        ),
        "action_policy": _build_action_policy_config(action_policy_payload),
    }


def _gateway_security_to_payload(config: OpenMinionConfig) -> dict[str, Any]:
    return {
        "gateway": {
            "host": config.gateway.host,
            "port": config.gateway.port,
            "api_turn_timeout_seconds": config.gateway.api_turn_timeout_seconds,
            "brain_integration_mode": _normalize_brain_integration_mode(
                config.gateway.brain_integration_mode
            ),
        },
        "channel_policy": {
            "dm_policy": config.channel_policy.dm_policy,
            "group_policy": config.channel_policy.group_policy,
            "dm_allowlist": list(config.channel_policy.dm_allowlist),
            "group_allowlist": list(config.channel_policy.group_allowlist),
            "paired_dm_senders": list(config.channel_policy.paired_dm_senders),
        },
        "channel_authenticity": {
            "mode": _normalize_channel_authenticity_mode(
                config.channel_authenticity.mode
            ),
            "trusted_channels": list(config.channel_authenticity.trusted_channels),
            "required_channels": list(config.channel_authenticity.required_channels),
            "secret_env_by_channel": dict(
                config.channel_authenticity.secret_env_by_channel
            ),
            "max_age_seconds": config.channel_authenticity.max_age_seconds,
            "allowed_algorithms": list(config.channel_authenticity.allowed_algorithms),
        },
        "security": {
            "tool_policy": {
                "default_required_scopes": list(
                    config.security.tool_policy.default_required_scopes
                ),
                "max_calls_per_run": config.security.tool_policy.max_calls_per_run,
                "max_calls_per_tool": config.security.tool_policy.max_calls_per_tool,
                "max_budget_cost_per_run": config.security.tool_policy.max_budget_cost_per_run,
            }
        },
        "action_policy": _action_policy_to_payload(config),
    }
