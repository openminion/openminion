import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

try:
    import yaml  # type: ignore[import-untyped]
except ModuleNotFoundError:  # pragma: no cover
    yaml = None

from openminion.base.config import OpenMinionConfig
from openminion.base.config.parse import _as_bool, _as_non_empty_str, _as_str_or_none
from openminion.base.config.paths import ensure_under_data_root
from openminion.modules.config import (
    is_module_standalone_mode,
    resolve_module_config_path,
    resolve_module_data_root,
    resolve_module_home_root,
)

from .constants import (
    DEFAULT_INTEGRATED_SQLITE_SUBPATH,
    DEFAULT_STANDALONE_SQLITE_SUBPATH,
    OPENMINION_DATA_ROOT_ENV,
    OPENMINION_MODULE_STANDALONE_ENV,
)

_ENV_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


@dataclass
class ControlPlaneConfig:
    sqlite_path: str = "~/.controlplane/cp.db"
    wal: bool = True
    default_agent_id: str = "agent:default"
    command_prefix: str = "/"
    idle_minutes: int = 0  # 0 = no auto-rotation
    admin_user_keys: list[str] = field(default_factory=list)
    store_backend: str = "sqlite"  # "sqlite" | "memory"
    openminion_enabled: bool = False
    openminion_config_path: str | None = None
    openminion_channel: str = "console"
    openminion_target: str = "controlplane"
    openminion_deliver: bool = False
    path_mode: str = "module_standalone"
    path_source: str = "standalone_default"
    home_root: str | None = None
    outbox_max_attempts: int = 8
    outbox_max_backoff_s: int = 300
    rate_limit_chat_window_s: int = 60
    rate_limit_chat_limit: int = 30
    rate_limit_user_window_s: int = 60
    rate_limit_user_limit: int = 30
    rate_limit_session_window_s: int = 60
    rate_limit_session_limit: int = 40
    audit_schema_validation_enabled: bool = False
    health_probe_enabled: bool = False
    health_probe_host: str = "127.0.0.1"
    health_probe_port: int = 9100
    health_probe_allow_remote: bool = False
    health_probe_bearer_token: str | None = None
    janitor_enabled: bool = True
    janitor_interval_seconds: int = 3600
    janitor_dry_run: bool = False
    audit_retention_days: int = 30
    outbox_terminal_retention_days: int = 7
    pair_token_retention_days: int = 30
    pair_attempt_retention_days: int = 90
    rate_limit_retention_days: int = 7
    wizard_terminal_retention_days: int = 30


def load_config(
    source: str | Path | dict[str, Any] | ControlPlaneConfig | None = None,
    *,
    home_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ControlPlaneConfig:
    env_map = dict(env or os.environ)
    standalone_mode = is_module_standalone_mode(env_map)
    resolved_home_root = (
        None if standalone_mode else resolve_module_home_root(home_root, env_map)
    )
    resolved_data_root = (
        resolve_module_data_root(home_root=resolved_home_root, env=env_map)
        if resolved_home_root is not None
        else None
    )
    path_mode = (
        "module_standalone"
        if standalone_mode
        else "integrated_runtime"
        if resolved_home_root
        else "module_standalone"
    )
    default_source = (
        "standalone_default"
        if path_mode == "module_standalone"
        else "default_integrated"
    )

    if source is None:
        return _default_config(
            home_root=resolved_home_root,
            data_root=resolved_data_root,
            path_mode=path_mode,
            path_source=default_source,
        )
    if isinstance(source, ControlPlaneConfig):
        return source
    if isinstance(source, dict):
        return _from_dict(
            source,
            home_root=resolved_home_root,
            data_root=resolved_data_root,
            env_map=env_map,
            default_path_mode=path_mode,
            default_path_source=default_source,
        )
    path = resolve_module_config_path(source, home_root=resolved_home_root)
    if not path.exists():
        return _default_config(
            home_root=resolved_home_root,
            data_root=resolved_data_root,
            path_mode=path_mode,
            path_source=default_source,
        )
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        raw = json.loads(text or "{}")
    elif yaml is not None:
        raw = yaml.safe_load(text) or {}
    else:
        raw = {}
    if not isinstance(raw, dict):
        return _default_config(
            home_root=resolved_home_root,
            data_root=resolved_data_root,
            path_mode=path_mode,
            path_source=default_source,
        )
    return _from_dict(
        raw.get("controlplane", raw),
        home_root=resolved_home_root,
        data_root=resolved_data_root,
        env_map=env_map,
        default_path_mode=path_mode,
        default_path_source=default_source,
    )


def from_base_config(
    *,
    base_config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
) -> ControlPlaneConfig:
    env = dict(getattr(base_config.runtime, "env", {}) or {})
    env.setdefault(OPENMINION_DATA_ROOT_ENV, str(data_root))
    env.pop(OPENMINION_MODULE_STANDALONE_ENV, None)
    channel_dict = getattr(base_config, "channels", {}).get("controlplane")
    if isinstance(channel_dict, dict):
        return load_config(channel_dict, home_root=home_root, env=env)
    return load_config(None, home_root=home_root, env=env)


def _default_config(
    *, home_root: Path | None, data_root: Path | None, path_mode: str, path_source: str
) -> ControlPlaneConfig:
    sqlite_path = _default_sqlite_path(home_root, data_root, path_mode=path_mode)
    return ControlPlaneConfig(
        sqlite_path=str(sqlite_path),
        path_mode=path_mode,
        path_source=path_source,
        home_root=str(home_root) if home_root else None,
    )


def _from_dict(
    raw: dict[str, Any],
    *,
    home_root: Path | None,
    data_root: Path | None,
    env_map: Mapping[str, str],
    default_path_mode: str,
    default_path_source: str,
) -> ControlPlaneConfig:
    sqlite_raw = _resolve_secret(raw.get("sqlite_path"), env_map=env_map)
    sqlite_path = _resolve_sqlite_path(
        sqlite_raw,
        home_root=home_root,
        data_root=data_root,
        default_path_mode=default_path_mode,
    )
    explicit_override = sqlite_raw is not None and str(sqlite_raw).strip() != ""
    path_source = "explicit_override" if explicit_override else default_path_source

    return ControlPlaneConfig(
        sqlite_path=str(sqlite_path),
        wal=bool(raw.get("wal", True)),
        default_agent_id=str(raw.get("default_agent_id", "agent:default")),
        command_prefix=str(raw.get("command_prefix", "/")),
        idle_minutes=int(raw.get("idle_minutes", 0)),
        admin_user_keys=[
            str(k) for k in raw.get("admin_user_keys", []) if str(k).strip()
        ],
        store_backend=str(raw.get("store_backend", "sqlite")),
        openminion_enabled=_as_bool(raw.get("openminion_enabled"), default=False),
        openminion_config_path=_as_str_or_none(
            _resolve_secret(raw.get("openminion_config_path"), env_map=env_map)
        ),
        openminion_channel=_as_non_empty_str(
            raw.get("openminion_channel"), default="console"
        ),
        openminion_target=_as_non_empty_str(
            raw.get("openminion_target"), default="controlplane"
        ),
        openminion_deliver=_as_bool(raw.get("openminion_deliver"), default=False),
        path_mode=default_path_mode,
        path_source=path_source,
        home_root=str(home_root) if home_root else None,
        outbox_max_attempts=int(raw.get("outbox_max_attempts", 8)),
        outbox_max_backoff_s=int(raw.get("outbox_max_backoff_s", 300)),
        rate_limit_chat_window_s=int(raw.get("rate_limit_chat_window_s", 60)),
        rate_limit_chat_limit=int(raw.get("rate_limit_chat_limit", 30)),
        rate_limit_user_window_s=int(raw.get("rate_limit_user_window_s", 60)),
        rate_limit_user_limit=int(raw.get("rate_limit_user_limit", 30)),
        rate_limit_session_window_s=int(raw.get("rate_limit_session_window_s", 60)),
        rate_limit_session_limit=int(raw.get("rate_limit_session_limit", 40)),
        audit_schema_validation_enabled=_as_bool(
            raw.get("audit_schema_validation_enabled"), default=False
        ),
        health_probe_enabled=_as_bool(raw.get("health_probe_enabled"), default=False),
        health_probe_host=_as_non_empty_str(
            raw.get("health_probe_host"), default="127.0.0.1"
        ),
        health_probe_port=int(raw.get("health_probe_port", 9100)),
        health_probe_allow_remote=_as_bool(
            raw.get("health_probe_allow_remote"), default=False
        ),
        health_probe_bearer_token=_as_str_or_none(
            _resolve_secret(raw.get("health_probe_bearer_token"), env_map=env_map)
        ),
        janitor_enabled=_as_bool(raw.get("janitor_enabled"), default=True),
        janitor_interval_seconds=int(raw.get("janitor_interval_seconds", 3600)),
        janitor_dry_run=_as_bool(raw.get("janitor_dry_run"), default=False),
        audit_retention_days=int(raw.get("audit_retention_days", 30)),
        outbox_terminal_retention_days=int(
            raw.get("outbox_terminal_retention_days", 7)
        ),
        pair_token_retention_days=int(raw.get("pair_token_retention_days", 30)),
        pair_attempt_retention_days=int(raw.get("pair_attempt_retention_days", 90)),
        rate_limit_retention_days=int(raw.get("rate_limit_retention_days", 7)),
        wizard_terminal_retention_days=int(
            raw.get("wizard_terminal_retention_days", 30)
        ),
    )


def _default_sqlite_path(
    home_root: Path | None,
    data_root: Path | None,
    *,
    path_mode: str,
) -> Path:
    if (
        home_root is not None
        and data_root is not None
        and path_mode == "integrated_runtime"
    ):
        return (data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve(strict=False)
    return (Path.home() / DEFAULT_STANDALONE_SQLITE_SUBPATH).resolve(strict=False)


def _resolve_sqlite_path(
    raw_value: Any,
    *,
    home_root: Path | None,
    data_root: Path | None,
    default_path_mode: str,
) -> Path:
    if raw_value is None or str(raw_value).strip() == "":
        return _default_sqlite_path(home_root, data_root, path_mode=default_path_mode)
    candidate = Path(str(raw_value)).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=False)
        if data_root is not None and default_path_mode == "integrated_runtime":
            return ensure_under_data_root(
                resolved, data_root, label="controlplane_sqlite_path"
            )
        return resolved
    if data_root is not None and default_path_mode == "integrated_runtime":
        resolved = (data_root / candidate).resolve(strict=False)
        return ensure_under_data_root(
            resolved, data_root, label="controlplane_sqlite_path"
        )
    if home_root is not None and default_path_mode == "integrated_runtime":
        return (home_root / candidate).resolve(strict=False)
    return candidate.resolve(strict=False)


def _resolve_secret(value: Any, *, env_map: Mapping[str, str]) -> str:
    raw = _as_non_empty_str(value, default="")
    if not raw:
        return ""
    match = _ENV_PATTERN.match(raw)
    if not match:
        return raw
    return str(env_map.get(match.group(1), "") or "")
