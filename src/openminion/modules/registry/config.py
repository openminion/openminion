from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal
from collections.abc import Mapping

import yaml
from pydantic import BaseModel, Field

from openminion.base.config import OpenMinionConfig
from openminion.base.config.paths import ensure_under_data_root
from openminion.modules.config import (
    is_module_standalone_mode,
    resolve_module_config_path,
    resolve_module_data_root,
    resolve_module_home_root,
)

from .constants import (
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_MANIFEST_FILENAME,
    DEFAULT_INTEGRATED_MANIFEST_SUBPATH,
    DEFAULT_INTEGRATED_SQLITE_SUBPATH,
    DEFAULT_STANDALONE_MANIFEST_SUBPATH,
    DEFAULT_STANDALONE_SQLITE_SUBPATH,
)


class StoreConfig(BaseModel):
    backend: Literal["sqlite", "memory"] = "sqlite"
    sqlite_path: str = str(DEFAULT_STANDALONE_SQLITE_SUBPATH)
    wal: bool = True
    path_mode: str = "module_standalone"
    path_source: str = "standalone_default"


class AgentRegistryConfig(BaseModel):
    manifest_path: str = DEFAULT_MANIFEST_FILENAME
    allow_runtime_override: bool = True
    store: StoreConfig = Field(default_factory=StoreConfig)
    home_root: str | None = None


class AppConfig(BaseModel):
    agentregctl: AgentRegistryConfig = Field(default_factory=AgentRegistryConfig)


def _resolve_path_context(
    *,
    home_root: Path | None,
    env: Mapping[str, str],
) -> tuple[Path | None, Path | None, str, str]:
    standalone_mode = is_module_standalone_mode(env)
    resolved_home_root = (
        None if standalone_mode else resolve_module_home_root(home_root, env)
    )
    resolved_data_root = (
        resolve_module_data_root(home_root=resolved_home_root, env=env)
        if resolved_home_root is not None
        else None
    )
    path_mode = "integrated_runtime" if resolved_home_root else "module_standalone"
    default_path_source = (
        "default_integrated"
        if path_mode == "integrated_runtime"
        else "standalone_default"
    )
    return resolved_home_root, resolved_data_root, path_mode, default_path_source


def from_base_config(
    *,
    base_config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
) -> AgentRegistryConfig:
    return _default_config(
        home_root=home_root,
        data_root=data_root,
        path_mode="integrated_runtime",
        path_source="default_integrated",
    )


def load_config(
    path: str | Path = DEFAULT_CONFIG_FILENAME,
    *,
    home_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> AgentRegistryConfig:
    env_map = dict(env or os.environ)
    resolved_home_root, resolved_data_root, path_mode, default_path_source = (
        _resolve_path_context(home_root=home_root, env=env_map)
    )

    cfg_path = resolve_module_config_path(path, home_root=resolved_home_root)
    if not cfg_path.exists():
        return _default_config(
            home_root=resolved_home_root,
            data_root=resolved_data_root,
            path_mode=path_mode,
            path_source=default_path_source,
        )

    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if raw is None:
        return _default_config(
            home_root=resolved_home_root,
            data_root=resolved_data_root,
            path_mode=path_mode,
            path_source=default_path_source,
        )

    if not isinstance(raw, dict):
        raise ValueError(f"Invalid config root in {cfg_path}; expected mapping")

    if "agentregctl" in raw:
        cfg = AppConfig.model_validate(raw).agentregctl
    else:
        cfg = AgentRegistryConfig.model_validate(raw)

    return _resolve_paths(
        cfg,
        home_root=resolved_home_root,
        data_root=resolved_data_root,
        path_mode=path_mode,
        default_path_source=default_path_source,
        explicit_config=True,
    )


def config_from_dict(
    raw: dict[str, Any],
    *,
    home_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> AgentRegistryConfig:
    env_map = dict(env or os.environ)
    resolved_home_root, resolved_data_root, path_mode, default_path_source = (
        _resolve_path_context(home_root=home_root, env=env_map)
    )

    cfg = (
        AppConfig.model_validate(raw).agentregctl
        if "agentregctl" in raw
        else AgentRegistryConfig.model_validate(raw)
    )
    return _resolve_paths(
        cfg,
        home_root=resolved_home_root,
        data_root=resolved_data_root,
        path_mode=path_mode,
        default_path_source=default_path_source,
        explicit_config=True,
    )


def _default_paths(
    home_root: Path | None,
    data_root: Path | None,
    path_mode: str,
) -> tuple[Path, Path]:
    if (
        home_root is not None
        and data_root is not None
        and path_mode == "integrated_runtime"
    ):
        return (
            (data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve(strict=False),
            (data_root / DEFAULT_INTEGRATED_MANIFEST_SUBPATH).resolve(strict=False),
        )
    return (
        DEFAULT_STANDALONE_SQLITE_SUBPATH.expanduser().resolve(strict=False),
        DEFAULT_STANDALONE_MANIFEST_SUBPATH.expanduser().resolve(strict=False),
    )


def _default_config(
    *,
    home_root: Path | None,
    data_root: Path | None,
    path_mode: str,
    path_source: str,
) -> AgentRegistryConfig:
    sqlite_path, manifest_path = _default_paths(home_root, data_root, path_mode)
    return AgentRegistryConfig(
        manifest_path=str(manifest_path),
        store=StoreConfig(
            sqlite_path=str(sqlite_path),
            path_mode=path_mode,
            path_source=path_source,
        ),
        home_root=str(home_root) if home_root else None,
    )


def _resolve_paths(
    cfg: AgentRegistryConfig,
    *,
    home_root: Path | None,
    data_root: Path | None,
    path_mode: str,
    default_path_source: str,
    explicit_config: bool,
) -> AgentRegistryConfig:
    default_sqlite, default_manifest = _default_paths(home_root, data_root, path_mode)
    resolve_base = (
        data_root
        if data_root is not None and path_mode == "integrated_runtime"
        else home_root
    )

    raw_sqlite = str(cfg.store.sqlite_path or "").strip()
    if raw_sqlite:
        sqlite_candidate = Path(raw_sqlite).expanduser()
        if not sqlite_candidate.is_absolute() and resolve_base is not None:
            sqlite_candidate = resolve_base / sqlite_candidate
        sqlite_path = sqlite_candidate.resolve(strict=False)
    else:
        sqlite_path = default_sqlite

    raw_manifest = str(cfg.manifest_path or "").strip()
    if raw_manifest:
        manifest_candidate = Path(raw_manifest).expanduser()
        if not manifest_candidate.is_absolute() and resolve_base is not None:
            manifest_candidate = resolve_base / manifest_candidate
        manifest_path = manifest_candidate.resolve(strict=False)
    else:
        manifest_path = default_manifest

    if data_root is not None and path_mode == "integrated_runtime":
        sqlite_path = ensure_under_data_root(
            sqlite_path, data_root, label="registry_sqlite_path"
        )
        manifest_path = ensure_under_data_root(
            manifest_path, data_root, label="registry_manifest_path"
        )

    explicit_override = explicit_config and (
        raw_sqlite not in {"", str(DEFAULT_STANDALONE_SQLITE_SUBPATH)}
        or raw_manifest not in {"", str(DEFAULT_STANDALONE_MANIFEST_SUBPATH)}
    )
    path_source = "explicit_override" if explicit_override else default_path_source

    return cfg.model_copy(
        update={
            "manifest_path": str(manifest_path),
            "home_root": str(home_root) if home_root else None,
            "store": cfg.store.model_copy(
                update={
                    "sqlite_path": str(sqlite_path),
                    "path_mode": path_mode,
                    "path_source": path_source,
                }
            ),
        }
    )
