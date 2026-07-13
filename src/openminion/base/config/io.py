"""Config path resolution and JSON load/save helpers."""

from __future__ import annotations

import json
from pathlib import Path

from openminion.base.config.base import (
    ConfigError,
    DEFAULT_CONFIG_DIR,
    DEFAULT_CONFIG_FILENAME,
)
from openminion.base.config.core import OpenMinionConfig
from openminion.base.config.env import EnvironmentConfig


def resolve_config_path(config_path: str | None, home_root: Path | None = None) -> Path:
    """Resolve config path with OpenMinion Home awareness."""
    config_p = Path(config_path).expanduser() if config_path else None

    if config_p is not None:
        if config_p.is_absolute():
            return config_p.resolve()

        return (Path.cwd() / config_p).resolve()

    if home_root is None:
        env_config = EnvironmentConfig.from_sources()
        env_home = env_config.openminion_home.strip()
        if env_home:
            home_root = Path(env_home).expanduser()

    config_root = _resolve_config_root(home_root)
    return (config_root / str(DEFAULT_CONFIG_FILENAME)).resolve()


def _resolve_config_root(home_root: Path | None) -> Path:
    env_config = EnvironmentConfig.from_sources()
    config_dir = Path(DEFAULT_CONFIG_DIR)
    env_root = env_config.openminion_config_root.strip()
    if env_root:
        candidate = Path(env_root).expanduser()
        if not candidate.is_absolute() and home_root:
            candidate = home_root / candidate
        return Path(candidate).resolve()
    if home_root:
        return (home_root / config_dir).resolve()
    return (Path.home() / config_dir).resolve()


def load_config(
    config_path: str | None = None, home_root: Path | None = None
) -> OpenMinionConfig:
    """Load config, resolving explicit relative paths from cwd."""
    path = resolve_config_path(config_path, home_root=home_root)
    if not path.exists():
        return OpenMinionConfig()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON config at {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Unable to read config at {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ConfigError(f"Config at {path} must be a JSON object")

    return OpenMinionConfig.from_dict(payload)


def save_config(
    config: OpenMinionConfig,
    config_path: str | None = None,
    *,
    home_root: Path | None = None,
) -> Path:
    path = resolve_config_path(config_path, home_root=home_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


__all__ = [
    "load_config",
    "resolve_config_path",
    "save_config",
]
