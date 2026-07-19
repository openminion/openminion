"""Home, data-root, and storage path resolution helpers."""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from openminion.base.constants import (
    BASE_BOOL_TRUE_VALUES,
    BASE_DEFAULT_CONFIG_DIRNAME,
    BASE_STATE_DB_FILENAME,
    BASE_STATE_DIRNAME,
    OPENMINION_DATA_ROOT_ENFORCEMENT_ENV,
    OPENMINION_DATA_ROOT_ENV,
    OPENMINION_HOME_ENV,
    OPENMINION_MODULE_STANDALONE_ENV,
)
from openminion.base.config.base import ConfigError, DEFAULT_CONFIG_FILENAME
from openminion.base.config.env import EnvironmentConfig


def resolve_data_root_enforcement_mode(
    *,
    env: Mapping[str, str] | None = None,
    env_var: str = OPENMINION_DATA_ROOT_ENFORCEMENT_ENV,
) -> str:
    """Resolve enforcement mode from EnvironmentConfig or process env."""
    if env is not None and isinstance(env, EnvironmentConfig):
        raw = env.get(env_var, "hard").strip().lower()
    else:
        raw = str((env or os.environ).get(env_var, "hard")).strip().lower()
    if raw in {"soft", "warn"}:
        return "soft"
    return "hard"


def ensure_under_data_root(
    path: Path | str,
    data_root: Path | str | None,
    *,
    label: str,
) -> Path:
    if data_root is None:
        return Path(path).expanduser().resolve(strict=False)

    resolved_path = Path(path).expanduser().resolve(strict=False)
    resolved_root = Path(data_root).expanduser().resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError:
        mode = resolve_data_root_enforcement_mode()
        message = (
            f"{label} must be under data_root ({resolved_root}), got {resolved_path}"
        )
        if mode == "soft":
            warnings.warn(message, RuntimeWarning)
            return resolved_path
        raise ConfigError(message)
    return resolved_path


def resolve_home_root(
    *,
    config_path: str | None = None,
    fallback: str = ".",
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the canonical OpenMinion home root from config, env, or cwd."""
    if env is not None and isinstance(env, EnvironmentConfig):
        env_home = env.openminion_home.strip()
    else:
        env_home = str((env or os.environ).get(OPENMINION_HOME_ENV, "")).strip()

    if env_home:
        return Path(env_home).expanduser().resolve()

    if config_path:
        config_p = Path(config_path)
        if config_p.is_absolute():
            return config_p.parent.resolve()

    return Path(fallback).resolve()


def resolve_data_root(
    home_root: Path,
    *,
    data_root: str | None = None,
    env_var: str = OPENMINION_DATA_ROOT_ENV,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the canonical data root under the chosen OpenMinion home."""
    if data_root:
        candidate = Path(data_root)
        if not candidate.is_absolute():
            candidate = home_root / candidate
        return candidate.resolve()

    env_source = env if env is not None else os.environ
    env_root = env_source.get(env_var, "").strip()
    if env_root:
        env_home = str(env_source.get(OPENMINION_HOME_ENV, "")).strip()
        if env_home:
            resolved_env_home = Path(env_home).expanduser().resolve(strict=False)
            resolved_home = Path(home_root).expanduser().resolve(strict=False)
            if resolved_env_home != resolved_home:
                env_root = ""
    if env_root:
        candidate = Path(env_root)
        if not candidate.is_absolute():
            candidate = home_root / candidate
        return candidate.resolve()

    return (home_root / str(BASE_DEFAULT_CONFIG_DIRNAME)).resolve()


def resolve_storage_paths(
    home_root: Path,
    *,
    data_root: str | None = None,
    config_filename: str = DEFAULT_CONFIG_FILENAME,
    storage_subdir: str = BASE_STATE_DIRNAME,
    db_filename: str = BASE_STATE_DB_FILENAME,
) -> tuple[Path, Path]:
    """Resolve the default config and storage paths under the data root."""
    resolved_data_root = resolve_data_root(home_root, data_root=data_root)
    config_path = resolved_data_root / config_filename
    storage_path = resolved_data_root / storage_subdir / db_filename
    config_path = ensure_under_data_root(
        config_path, resolved_data_root, label="config_path"
    )
    storage_path = ensure_under_data_root(
        storage_path, resolved_data_root, label="storage_path"
    )
    return config_path.resolve(strict=False), storage_path.resolve(strict=False)


def resolve_module_storage_path(
    home_root: Path,
    module_name: str,
    *,
    data_root: str | None = None,
    filename: str | None = None,
    subdir: str | None = None,
) -> Path:
    """Resolve a module-owned storage path under the canonical data root."""
    resolved_data_root = resolve_data_root(home_root, data_root=data_root)
    module_root = resolved_data_root / module_name

    if subdir:
        module_root = module_root / subdir

    if filename:
        return module_root / filename

    return module_root / f"{module_name}.db"


@dataclass
class HomePaths:
    """Resolved home/data paths with source metadata."""

    home_root: Path
    data_root: Path
    config_path: Path
    storage_path: Path
    path_mode: str = "integrated_runtime"
    path_source: str = "default_integrated"

    def to_debug_dict(self) -> dict[str, Any]:
        """Return debug-serializable dict with path information."""
        return {
            "home_root": str(self.home_root),
            "data_root": str(self.data_root),
            "config_path": str(self.config_path),
            "storage_path": str(self.storage_path),
            "path_mode": self.path_mode,
            "path_source": self.path_source,
        }


def resolve_config_storage_path(
    value: str, *, data_root: Path | None, label: str
) -> str:
    """Resolve a storage path string relative to data_root, enforcing boundary."""
    candidate = Path(value).expanduser()
    if data_root is None:
        return str(candidate.resolve(strict=False))
    if not candidate.is_absolute():
        candidate = data_root / candidate
    resolved = ensure_under_data_root(candidate, data_root, label=label)
    return str(resolved.resolve(strict=False))


def bootstrap_home_paths(
    *,
    config_path: str | None = None,
    workspace_root: str | None = None,
    data_root: str | None = None,
) -> HomePaths:
    """Bootstrap home/data/config paths for integrated or standalone runtime."""
    path_source = "default_integrated"
    path_mode = "integrated_runtime"
    explicit_home_override = bool(workspace_root or config_path)

    if workspace_root:
        path_source = "explicit_workspace"
        home_root = Path(workspace_root).expanduser().resolve()
    elif config_path:
        path_source = "explicit_config"
        config_p = Path(config_path)
        if not config_p.is_absolute():
            home_root = Path.cwd().resolve()
        else:
            home_root = config_p.parent.resolve()
    else:
        home_root = resolve_home_root(config_path=config_path)

        if (
            os.getenv(OPENMINION_MODULE_STANDALONE_ENV, "").strip().lower()
            in BASE_BOOL_TRUE_VALUES
        ):
            path_mode = "module_standalone"
            path_source = "env_standalone"

    env_root = os.getenv(OPENMINION_HOME_ENV, "").strip()
    if env_root and not explicit_home_override:
        home_root = Path(env_root).expanduser().resolve()
        path_source = "env_var"

    data_root_path = resolve_data_root(home_root, data_root=data_root)
    config_p, storage_p = resolve_storage_paths(home_root, data_root=data_root)

    return HomePaths(
        home_root=home_root,
        data_root=data_root_path,
        config_path=config_p,
        storage_path=storage_p,
        path_mode=path_mode,
        path_source=path_source,
    )


__all__ = [
    "HomePaths",
    "bootstrap_home_paths",
    "resolve_config_storage_path",
    "resolve_data_root",
    "resolve_module_storage_path",
    "resolve_home_root",
    "resolve_storage_paths",
]
