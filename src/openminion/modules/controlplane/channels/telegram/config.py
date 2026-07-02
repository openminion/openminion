import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]
except ModuleNotFoundError:  # pragma: no cover
    yaml = None

from openminion.base.config import OpenMinionConfig
from openminion.base.config.base import ConfigError
from openminion.base.config.env import resolve_environment_config
from openminion.base.config.parse import (
    _as_bool,
    _as_int,
    _as_non_empty_str,
    _as_str_or_none,
)
from openminion.base.config.paths import ensure_under_data_root
from openminion.modules.config import (
    is_module_standalone_mode,
    resolve_module_config_path,
    resolve_module_data_root,
    resolve_module_home_root,
)
from openminion.modules.controlplane.constants import DEFAULT_MINIMAL_SCOPES

from .constants import (
    ALLOWED_MODES,
    ALLOWED_POLICIES,
    DEFAULT_HOME_ROOT_POLL_STATE_SUBPATH,
    DEFAULT_INTEGRATED_POLL_STATE_SUBPATH,
    DEFAULT_STANDALONE_POLL_STATE_SUBPATH,
    OPENMINION_DATA_ROOT_ENV,
    OPENMINION_MODULE_STANDALONE_ENV,
    PAIRING_MODE_OFF,
    PAIRING_MODE_REQUIRED,
    PAIRING_MODES,
    REPLY_MODE_TO_USER,
    REPLY_MODES,
)

_ENV_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


@dataclass(frozen=True)
class PollingConfig:
    timeout_seconds: int = 30
    limit: int = 100
    backoff_seconds: list[int] = field(default_factory=lambda: [1, 2, 4, 8, 16])
    persist_offset: bool = True
    drop_pending_on_start: bool = False
    state_sqlite_path: str = "~/.controlplane/telegram-poll-state.db"
    path_mode: str = "module_standalone"
    path_source: str = "standalone_default"
    home_root: str | None = None


@dataclass(frozen=True)
class WebhookConfig:
    enabled: bool = False
    url: str | None = None
    secret: str | None = None
    drop_pending_updates: bool = True
    # optional in-tree HTTP listener. bind_port == 0 means
    bind_host: str = "127.0.0.1"
    bind_port: int = 0

    def __post_init__(self) -> None:
        if not self.enabled:
            return
        secret = str(self.secret or "").strip()
        if not secret:
            raise ConfigError("webhook.enabled=True requires non-empty webhook.secret")


@dataclass(frozen=True)
class AccessConfig:
    dm_policy: str = "allowlist"
    allow_from_user_ids: list[int] = field(default_factory=list)
    group_policy: str = "deny"
    allow_group_chat_ids: list[int] = field(default_factory=list)
    mention_only_in_groups: bool = True
    allowed_topic_ids_by_chat: dict[str, list[int]] = field(default_factory=dict)


@dataclass(frozen=True)
class PairingConfig:
    enabled: bool = True
    mode: str = PAIRING_MODE_REQUIRED
    code_length: int = 8
    token_ttl_seconds: int = 600
    pending_cap_per_channel: int = 3
    attempt_window_seconds: int = 60
    max_attempts_per_user: int = 6
    max_attempts_per_chat: int = 20
    hash_pepper: str | None = None
    allow_in_groups: bool = False
    default_scopes: list[str] = field(
        default_factory=lambda: list(DEFAULT_MINIMAL_SCOPES)
    )


@dataclass(frozen=True)
class ReplyConfig:
    mode: str = REPLY_MODE_TO_USER


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 3
    backoff_ms: list[int] = field(default_factory=lambda: [250, 750, 2000])


@dataclass(frozen=True)
class DeliveryConfig:
    parse_mode: str = "plain"  # MarkdownV2 | HTML | plain
    link_preview: bool = True
    chunk_limit: int = 3500
    retry: RetryConfig = field(default_factory=RetryConfig)


@dataclass(frozen=True)
class ActionsConfig:
    send_message: bool = True
    edit_message: bool = True
    reactions: bool = True
    inline_buttons: bool = True


@dataclass(frozen=True)
class ClarifyConfig:
    enabled: bool = True
    mode: str = "reply"  # reply | inline_keyboard
    max_questions_per_message: int = 2
    answer_prefix: str = "/clarify"


@dataclass(frozen=True)
class TelegramChannelConfig:
    enabled: bool = False
    bot_token: str = ""
    mode: str = "polling"
    allowed_updates: list[str] = field(
        default_factory=lambda: ["message", "edited_message", "callback_query"]
    )
    polling: PollingConfig = field(default_factory=PollingConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    access: AccessConfig = field(default_factory=AccessConfig)
    pairing: PairingConfig = field(default_factory=PairingConfig)
    reply: ReplyConfig = field(default_factory=ReplyConfig)
    delivery: DeliveryConfig = field(default_factory=DeliveryConfig)
    actions: ActionsConfig = field(default_factory=ActionsConfig)
    clarify: ClarifyConfig = field(default_factory=ClarifyConfig)


@dataclass(frozen=True)
class ControlplaneTelegramConfig:
    telegram: TelegramChannelConfig = field(default_factory=TelegramChannelConfig)


def load_config(
    source: str | Path | dict[str, Any] | ControlplaneTelegramConfig | None = None,
    *,
    home_root: Path | None = None,
    env: dict[str, str] | None = None,
) -> ControlplaneTelegramConfig:
    env_map = dict(env) if env is not None else resolve_environment_config().snapshot()
    standalone_mode = is_module_standalone_mode(env_map)
    resolved_home_root = (
        None if standalone_mode else resolve_module_home_root(home_root, env_map)
    )
    resolved_data_root = (
        resolve_module_data_root(home_root=resolved_home_root, env=env_map)
        if resolved_home_root is not None
        else None
    )

    if source is None:
        return _default_config(
            resolved_home_root,
            resolved_data_root,
            standalone_mode=standalone_mode,
            env_map=env_map,
        )
    if isinstance(source, ControlplaneTelegramConfig):
        return source
    if isinstance(source, dict):
        return _from_dict(
            source,
            home_root=resolved_home_root,
            data_root=resolved_data_root,
            standalone_mode=standalone_mode,
            env_map=env_map,
        )

    path = resolve_module_config_path(source, home_root=resolved_home_root)
    if not path.exists():
        return _default_config(
            resolved_home_root,
            resolved_data_root,
            standalone_mode=standalone_mode,
            env_map=env_map,
        )
    raw_text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        parsed = json.loads(raw_text or "{}")
    elif yaml is not None:
        parsed = yaml.safe_load(raw_text) or {}
    else:
        parsed = {}
    if not isinstance(parsed, dict):
        return _default_config(
            resolved_home_root,
            resolved_data_root,
            standalone_mode=standalone_mode,
            env_map=env_map,
        )
    return _from_dict(
        parsed,
        home_root=resolved_home_root,
        data_root=resolved_data_root,
        standalone_mode=standalone_mode,
        env_map=env_map,
    )


def from_base_config(
    *,
    base_config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
) -> ControlplaneTelegramConfig:
    env = dict(getattr(base_config.runtime, "env", {}) or {})
    env.setdefault(OPENMINION_DATA_ROOT_ENV, str(data_root))
    env.pop(OPENMINION_MODULE_STANDALONE_ENV, None)
    channel_dict = getattr(base_config, "channels", {}).get("telegram")
    if isinstance(channel_dict, dict):
        return load_config(
            {"channels": {"telegram": channel_dict}},
            home_root=home_root,
            env=env,
        )
    cfg = load_config(None, home_root=home_root, env=env)
    legacy_bot_token = str(env.get("TELEGRAM_BOT_TOKEN", "")).strip()
    if legacy_bot_token:
        return ControlplaneTelegramConfig(
            telegram=replace(
                cfg.telegram,
                enabled=True,
                bot_token=legacy_bot_token,
            )
        )
    return cfg


def _from_dict(
    raw: dict[str, Any],
    *,
    home_root: Path | None,
    data_root: Path | None,
    standalone_mode: bool,
    env_map: dict[str, str],
) -> ControlplaneTelegramConfig:
    t_root = _telegram_root(raw)
    parts = _telegram_raw_parts(t_root)
    mode, reply_mode, parse_mode, clarify_mode = _telegram_modes(parts)
    polling = _polling_config_from_raw(
        parts["polling"],
        home_root=home_root,
        data_root=data_root,
        standalone_mode=standalone_mode,
        env_map=env_map,
    )
    return ControlplaneTelegramConfig(
        telegram=TelegramChannelConfig(
            enabled=_as_bool(t_root.get("enabled"), default=False),
            bot_token=_resolve_secret(
                _first_non_none(t_root.get("botToken"), t_root.get("bot_token")),
                env_map=env_map,
            ),
            mode=mode,
            allowed_updates=_allowed_updates(t_root, parts["polling"]),
            polling=polling,
            webhook=_webhook_config_from_raw(parts["webhook"], env_map=env_map),
            access=_access_config_from_raw(parts["access"], parts["groups"]),
            pairing=_pairing_config_from_raw(parts["pairing"], env_map=env_map),
            reply=ReplyConfig(mode=reply_mode),
            delivery=_delivery_config_from_raw(
                parts["delivery"], parts["retry"], parse_mode
            ),
            actions=ActionsConfig(
                send_message=_as_bool(
                    parts["actions"].get("sendMessage"), default=True
                ),
                edit_message=_as_bool(
                    parts["actions"].get("editMessage"), default=True
                ),
                reactions=_as_bool(parts["actions"].get("reactions"), default=True),
                inline_buttons=_as_bool(
                    parts["actions"].get("inlineButtons"), default=True
                ),
            ),
            clarify=_clarify_config_from_raw(parts["clarify"], clarify_mode),
        )
    )


def _telegram_root(raw: dict[str, Any]) -> dict[str, Any]:
    channels = raw.get("channels")
    telegram_channel = channels.get("telegram") if isinstance(channels, dict) else None
    if isinstance(telegram_channel, dict):
        return telegram_channel
    telegram_root = raw.get("telegram")
    if isinstance(telegram_root, dict):
        return telegram_root
    dotted_telegram = raw.get("channels.telegram")
    if isinstance(dotted_telegram, dict):
        return dotted_telegram
    return raw


def _telegram_raw_parts(t_root: dict[str, Any]) -> dict[str, dict[str, Any]]:
    delivery_raw = _as_dict(t_root.get("delivery"))
    return {
        "root": t_root,
        "polling": _as_dict(t_root.get("polling")),
        "webhook": _as_dict(t_root.get("webhook")),
        "access": _as_dict(t_root.get("access")),
        "pairing": _as_dict(t_root.get("pairing")),
        "reply": _as_dict(t_root.get("reply")),
        "delivery": delivery_raw,
        "retry": _as_dict(delivery_raw.get("retry")),
        "actions": _as_dict(t_root.get("actions")),
        "groups": _as_dict(t_root.get("groups")),
        "clarify": _as_dict(t_root.get("clarify")),
    }


def _telegram_modes(parts: dict[str, dict[str, Any]]) -> tuple[str, str, str, str]:
    mode = _as_non_empty_str(
        parts.get("root", {}).get("mode"), default="polling"
    ).lower()
    # Caller patches the root mode after this fallback when root is not in parts.
    mode = mode if mode in ALLOWED_MODES else "polling"
    reply_mode = _as_non_empty_str(
        parts["reply"].get("mode"), default=REPLY_MODE_TO_USER
    )
    if reply_mode not in REPLY_MODES:
        reply_mode = REPLY_MODE_TO_USER
    parse_mode = _as_non_empty_str(parts["delivery"].get("parseMode"), default="plain")
    if parse_mode not in {"MarkdownV2", "HTML", "plain"}:
        parse_mode = "plain"
    clarify_mode = _as_non_empty_str(parts["clarify"].get("mode"), default="reply")
    if clarify_mode not in {"reply", "inline_keyboard"}:
        clarify_mode = "reply"
    return mode, reply_mode, parse_mode, clarify_mode


def _allowed_updates(t_root: dict[str, Any], polling_raw: dict[str, Any]) -> list[str]:
    allowed_updates = _as_list_str(t_root.get("allowedUpdates"))
    if not allowed_updates:
        allowed_updates = _as_list_str(polling_raw.get("allowed_updates"))
    if not allowed_updates:
        allowed_updates = _as_list_str(polling_raw.get("allowedUpdates"))
    return [item for item in allowed_updates if item] or [
        "message",
        "edited_message",
        "callback_query",
    ]


def _polling_config_from_raw(
    polling_raw: dict[str, Any],
    *,
    home_root: Path | None,
    data_root: Path | None,
    standalone_mode: bool,
    env_map: dict[str, str],
) -> PollingConfig:
    state_path, path_mode, path_source = _resolve_polling_state_sqlite_path(
        _first_non_none(
            polling_raw.get("stateSqlitePath"), polling_raw.get("state_sqlite_path")
        ),
        home_root=home_root,
        data_root=data_root,
        standalone_mode=standalone_mode,
        env_map=env_map,
    )
    return PollingConfig(
        timeout_seconds=max(
            0,
            _as_int(
                _first_non_none(
                    polling_raw.get("timeoutSeconds"), polling_raw.get("timeout_s")
                ),
                default=30,
            ),
        ),
        limit=max(1, min(100, _as_int(polling_raw.get("limit"), default=100))),
        backoff_seconds=_normalized_backoff_seconds(polling_raw),
        persist_offset=_as_bool(
            _first_non_none(
                polling_raw.get("persistOffset"), polling_raw.get("persist_offset")
            ),
            default=True,
        ),
        drop_pending_on_start=_as_bool(
            _first_non_none(
                polling_raw.get("dropPendingOnStart"),
                polling_raw.get("drop_pending_on_start"),
            ),
            default=False,
        ),
        state_sqlite_path=str(state_path),
        path_mode=path_mode,
        path_source=path_source,
        home_root=str(home_root) if home_root else None,
    )


def _normalized_backoff_seconds(polling_raw: dict[str, Any]) -> list[int]:
    values = _as_list_int(
        _first_non_none(
            polling_raw.get("backoffSeconds"),
            polling_raw.get("backoff_s"),
            polling_raw.get("backoffS"),
            polling_raw.get("backoffMs"),
        )
    ) or [1, 2, 4, 8, 16]
    if any(value > 60 for value in values):
        return [max(1, int(round(value / 1000))) for value in values]
    return values


def _webhook_config_from_raw(
    raw: dict[str, Any], *, env_map: dict[str, str]
) -> WebhookConfig:
    return WebhookConfig(
        enabled=_as_bool(raw.get("enabled"), default=False),
        url=_as_str_or_none(raw.get("url")),
        secret=_resolve_secret_or_none(raw.get("secret"), env_map=env_map),
        drop_pending_updates=_as_bool(raw.get("dropPendingUpdates"), default=True),
        bind_host=_as_non_empty_str(
            _first_non_none(raw.get("bindHost"), raw.get("bind_host")),
            default="127.0.0.1",
        ),
        bind_port=max(
            0,
            _as_int(
                _first_non_none(raw.get("bindPort"), raw.get("bind_port")), default=0
            ),
        ),
    )


def _access_config_from_raw(
    access_raw: dict[str, Any], groups_raw: dict[str, Any]
) -> AccessConfig:
    if "mentionOnlyInGroups" not in access_raw and "require_mention" in groups_raw:
        access_raw["mentionOnlyInGroups"] = groups_raw.get("require_mention")
    if "groupPolicy" not in access_raw and groups_raw.get("enabled") is False:
        access_raw["groupPolicy"] = "deny"
    dm_policy = _policy_value(access_raw.get("dmPolicy"), default="allowlist")
    group_policy = _policy_value(access_raw.get("groupPolicy"), default="deny")
    return AccessConfig(
        dm_policy=dm_policy,
        allow_from_user_ids=_as_list_int(access_raw.get("allowFromUserIds")),
        group_policy=group_policy,
        allow_group_chat_ids=_as_list_int(access_raw.get("allowGroupChatIds")),
        mention_only_in_groups=_as_bool(
            access_raw.get("mentionOnlyInGroups"), default=True
        ),
        allowed_topic_ids_by_chat=_access_topics(access_raw),
    )


def _policy_value(raw: Any, *, default: str) -> str:
    value = _as_non_empty_str(raw, default=default).lower()
    return value if value in ALLOWED_POLICIES else default


def _access_topics(access_raw: dict[str, Any]) -> dict[str, list[int]]:
    topics: dict[str, list[int]] = {}
    for chat_key, topic_list in _as_dict(
        access_raw.get("allowedTopicIdsByChat")
    ).items():
        values = []
        if isinstance(topic_list, list):
            values = [
                coerced
                for value in topic_list
                if (coerced := _as_int(value, default=0))
            ]
        topics[str(chat_key)] = values
    return topics


def _pairing_config_from_raw(
    raw: dict[str, Any], *, env_map: dict[str, str]
) -> PairingConfig:
    mode = _as_non_empty_str(
        _first_non_none(raw.get("mode"), raw.get("pair_mode")),
        default=PAIRING_MODE_REQUIRED,
    ).lower()
    if mode not in PAIRING_MODES:
        mode = PAIRING_MODE_REQUIRED
    return PairingConfig(
        enabled=_as_bool(raw.get("enabled"), default=(mode != PAIRING_MODE_OFF)),
        mode=mode,
        code_length=max(4, min(12, _as_int(raw.get("code_len"), default=8))),
        token_ttl_seconds=max(
            60,
            _as_int(
                _first_non_none(raw.get("tokenTtlSeconds"), raw.get("code_ttl_s")),
                default=600,
            ),
        ),
        pending_cap_per_channel=max(
            1,
            _as_int(
                _first_non_none(
                    raw.get("pending_cap_per_channel"), raw.get("pendingCapPerChannel")
                ),
                default=3,
            ),
        ),
        attempt_window_seconds=max(
            1, _as_int(raw.get("attemptWindowSeconds"), default=60)
        ),
        max_attempts_per_user=max(1, _as_int(raw.get("maxAttemptsPerUser"), default=6)),
        max_attempts_per_chat=max(
            1, _as_int(raw.get("maxAttemptsPerChat"), default=20)
        ),
        hash_pepper=_resolve_secret_or_none(raw.get("hashPepper"), env_map=env_map),
        allow_in_groups=_as_bool(raw.get("allowInGroups"), default=False),
        default_scopes=_as_list_str(raw.get("defaultScopes"))
        or list(DEFAULT_MINIMAL_SCOPES),
    )


def _delivery_config_from_raw(
    raw: dict[str, Any], retry_raw: dict[str, Any], parse_mode: str
) -> DeliveryConfig:
    return DeliveryConfig(
        parse_mode=parse_mode,
        link_preview=_as_bool(raw.get("linkPreview"), default=True),
        chunk_limit=max(256, _as_int(raw.get("chunkLimit"), default=3500)),
        retry=RetryConfig(
            max_attempts=max(1, _as_int(retry_raw.get("maxAttempts"), default=3)),
            backoff_ms=_as_list_int(retry_raw.get("backoffMs")) or [250, 750, 2000],
        ),
    )


def _clarify_config_from_raw(raw: dict[str, Any], mode: str) -> ClarifyConfig:
    return ClarifyConfig(
        enabled=_as_bool(raw.get("enabled"), default=True),
        mode=mode,
        max_questions_per_message=max(
            1,
            _as_int(
                _first_non_none(
                    raw.get("maxQuestionsPerMessage"),
                    raw.get("max_questions_per_message"),
                ),
                default=2,
            ),
        ),
        answer_prefix=_as_non_empty_str(
            _first_non_none(raw.get("answerPrefix"), raw.get("answer_prefix")),
            default="/clarify",
        ),
    )


def _default_config(
    home_root: Path | None,
    data_root: Path | None,
    *,
    standalone_mode: bool,
    env_map: dict[str, str],
) -> ControlplaneTelegramConfig:
    state_sqlite, path_mode, path_source = _resolve_polling_state_sqlite_path(
        None,
        home_root=home_root,
        data_root=data_root,
        standalone_mode=standalone_mode,
        env_map=env_map,
    )
    cfg = TelegramChannelConfig(
        polling=PollingConfig(
            state_sqlite_path=str(state_sqlite),
            path_mode=path_mode,
            path_source=path_source,
            home_root=str(home_root) if home_root else None,
        )
    )
    return ControlplaneTelegramConfig(telegram=cfg)


def _resolve_polling_state_sqlite_path(
    value: Any,
    *,
    home_root: Path | None,
    data_root: Path | None,
    standalone_mode: bool,
    env_map: dict[str, str],
) -> tuple[Path, str, str]:
    if standalone_mode:
        mode = "module_standalone"
        source = "standalone_default" if value is None else "explicit_override"
        if value is None or str(value).strip() == "":
            return (
                (Path.home() / DEFAULT_STANDALONE_POLL_STATE_SUBPATH).resolve(
                    strict=False
                ),
                mode,
                source,
            )
        candidate = Path(str(value)).expanduser()
        return candidate.resolve(strict=False), mode, source

    mode = "integrated_runtime" if home_root else "module_standalone"
    if data_root is None and home_root is not None:
        data_root = resolve_module_data_root(home_root=home_root, env=env_map)
    if value is None or str(value).strip() == "":
        if home_root is not None:
            resolved = (
                (data_root / DEFAULT_INTEGRATED_POLL_STATE_SUBPATH)
                if data_root is not None
                else (home_root / DEFAULT_HOME_ROOT_POLL_STATE_SUBPATH)
            ).resolve(strict=False)
            if data_root is not None:
                resolved = ensure_under_data_root(
                    resolved, data_root, label="controlplane_telegram_poll_state"
                )
            return (resolved, mode, "default_integrated")
        return (
            (Path.home() / DEFAULT_STANDALONE_POLL_STATE_SUBPATH).resolve(strict=False),
            mode,
            "standalone_default",
        )

    candidate = Path(str(value)).expanduser()
    if not candidate.is_absolute():
        if data_root is not None:
            candidate = data_root / candidate
        elif home_root is not None:
            candidate = home_root / candidate
    resolved = candidate.resolve(strict=False)
    if data_root is not None:
        resolved = ensure_under_data_root(
            resolved, data_root, label="controlplane_telegram_poll_state"
        )
    return resolved, mode, "explicit_override"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list_str(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _as_list_int(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _resolve_secret(value: Any, *, env_map: dict[str, str]) -> str:
    raw = _as_non_empty_str(value, default="")
    if not raw:
        return ""
    match = _ENV_PATTERN.match(raw)
    if not match:
        return raw
    return env_map.get(match.group(1), "")


def _resolve_secret_or_none(value: Any, *, env_map: dict[str, str]) -> str | None:
    if value is None:
        return None
    resolved = _resolve_secret(value, env_map=env_map)
    return resolved or None


def _first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None
