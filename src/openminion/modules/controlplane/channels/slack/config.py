"""Slack channel configuration parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from openminion.base.config import OpenMinionConfig
from openminion.base.config.env import resolve_environment_config
from openminion.base.config.parse import _as_bool, _as_int
from openminion.modules.controlplane.channels.slack.constants import (
    DEFAULT_MAX_MESSAGE_CHARS,
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_BACKOFF_S,
    DEFAULT_STATE_DB_NAME,
    MODE_SOCKET,
    SUPPORTED_MODES,
)


@dataclass(frozen=True)
class SlackRetryConfig:
    max_attempts: int = DEFAULT_RETRY_ATTEMPTS
    backoff_seconds: float = DEFAULT_RETRY_BACKOFF_S


@dataclass(frozen=True)
class SlackDeliveryConfig:
    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS
    retry: SlackRetryConfig = field(default_factory=SlackRetryConfig)


@dataclass(frozen=True)
class SlackAccessConfig:
    require_pairing: bool = True
    allow_dms: bool = True
    allow_broad_channel_messages: bool = False
    allowed_team_ids: tuple[str, ...] = ()
    allowed_channel_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlackPairingConfig:
    enabled: bool = True
    mode: str = "dm"
    token_ttl_seconds: int = 600
    default_scopes: tuple[str, ...] = (
        "cp.message.read",
        "cp.message.write",
        "session.read",
        "session.write",
        "run.start",
    )


@dataclass(frozen=True)
class SlackChannelConfig:
    enabled: bool = False
    mode: str = MODE_SOCKET
    bot_token: str = ""
    app_token: str = ""
    signing_secret: str = ""
    state_sqlite_path: str = ""
    access: SlackAccessConfig = field(default_factory=SlackAccessConfig)
    pairing: SlackPairingConfig = field(default_factory=SlackPairingConfig)
    delivery: SlackDeliveryConfig = field(default_factory=SlackDeliveryConfig)


@dataclass(frozen=True)
class ControlplaneSlackConfig:
    slack: SlackChannelConfig = field(default_factory=SlackChannelConfig)


def from_base_config(
    *,
    base_config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
    env: Mapping[str, str] | None = None,
) -> ControlplaneSlackConfig:
    raw_channels = dict(getattr(base_config, "channels", {}) or {})
    raw = dict(raw_channels.get("slack") or {})
    enabled_channels = {
        str(item).strip().lower()
        for item in getattr(base_config, "enabled_channels", []) or []
        if str(item).strip()
    }
    if "slack" in enabled_channels:
        raw["enabled"] = True
    env_map = dict(env or resolve_environment_config().snapshot())
    return ControlplaneSlackConfig(
        slack=_from_dict(raw, data_root=data_root, env=env_map)
    )


def load_config(
    config_path: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> ControlplaneSlackConfig:
    from openminion.cli.config import load_cli_config, resolve_cli_roots

    base = load_cli_config(config_path)
    roots = resolve_cli_roots(config_path=config_path)
    return from_base_config(
        base_config=base,
        home_root=roots.home_root,
        data_root=roots.data_root,
        env=env,
    )


def _from_dict(
    raw: Mapping[str, Any], *, data_root: Path, env: Mapping[str, str]
) -> SlackChannelConfig:
    mode = str(raw.get("mode") or MODE_SOCKET).strip().lower()
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"channels.slack.mode must be one of {sorted(SUPPORTED_MODES)}")
    state_path = str(
        raw.get("stateSqlitePath")
        or raw.get("state_sqlite_path")
        or data_root / "controlplane" / DEFAULT_STATE_DB_NAME
    )
    return SlackChannelConfig(
        enabled=_as_bool(raw.get("enabled"), default=False),
        mode=mode,
        bot_token=_resolve_secret(raw.get("botToken") or raw.get("bot_token"), env),
        app_token=_resolve_secret(raw.get("appToken") or raw.get("app_token"), env),
        signing_secret=_resolve_secret(
            raw.get("signingSecret") or raw.get("signing_secret"), env
        ),
        state_sqlite_path=state_path,
        access=_access_from_dict(raw.get("access") or {}),
        pairing=_pairing_from_dict(raw.get("pairing") or {}),
        delivery=_delivery_from_dict(raw.get("delivery") or {}),
    )


def _resolve_secret(raw: Any, env: Mapping[str, str]) -> str:
    value = str(raw or "").strip()
    if value.startswith("${") and value.endswith("}") and len(value) > 3:
        return str(env.get(value[2:-1], "")).strip()
    return value


def _tuple(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    return tuple(str(item).strip() for item in raw if str(item).strip())


def _access_from_dict(raw: Mapping[str, Any]) -> SlackAccessConfig:
    return SlackAccessConfig(
        require_pairing=_as_bool(raw.get("requirePairing"), default=True),
        allow_dms=_as_bool(raw.get("allowDms"), default=True),
        allow_broad_channel_messages=_as_bool(
            raw.get("allowBroadChannelMessages"), default=False
        ),
        allowed_team_ids=_tuple(raw.get("allowedTeamIds")),
        allowed_channel_ids=_tuple(raw.get("allowedChannelIds")),
    )


def _pairing_from_dict(raw: Mapping[str, Any]) -> SlackPairingConfig:
    return SlackPairingConfig(
        enabled=_as_bool(raw.get("enabled"), default=True),
        mode=str(raw.get("mode") or "dm"),
        token_ttl_seconds=_as_int(raw.get("tokenTtlSeconds"), default=600),
        default_scopes=_tuple(raw.get("defaultScopes"))
        or SlackPairingConfig.default_scopes,
    )


def _delivery_from_dict(raw: Mapping[str, Any]) -> SlackDeliveryConfig:
    retry_raw = raw.get("retry") or {}
    return SlackDeliveryConfig(
        max_message_chars=_as_int(
            raw.get("maxMessageChars"), default=DEFAULT_MAX_MESSAGE_CHARS
        ),
        retry=SlackRetryConfig(
            max_attempts=_as_int(
                retry_raw.get("maxAttempts"), default=DEFAULT_RETRY_ATTEMPTS
            ),
            backoff_seconds=float(
                retry_raw.get("backoffSeconds") or DEFAULT_RETRY_BACKOFF_S
            ),
        ),
    )
