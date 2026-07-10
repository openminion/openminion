from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openminion.base.config import ConfigManager, OpenMinionConfig
from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.base.config.paths import resolve_data_root, resolve_home_root

from .bootstrap.paths import (
    CLI_IDENTITY_DB_FILENAME,
    CLI_IDENTITY_SUBDIR,
    CLI_POLICY_DB_FILENAME,
    CLI_POLICY_SUBDIR,
)

CLIEnv = EnvironmentConfig


@dataclass(frozen=True)
class CLIRoots:
    env: EnvironmentConfig
    home_root: Path
    data_root: Path


def infer_workspace_home_root(cwd: Path) -> Path | None:
    resolved_cwd = cwd.resolve()
    if (resolved_cwd / "openminion").is_dir() and (
        resolved_cwd / "test-configs"
    ).is_dir():
        return resolved_cwd
    if resolved_cwd.name == "openminion":
        parent = resolved_cwd.parent.resolve()
        if (parent / "openminion").resolve() == resolved_cwd and (
            parent / "test-configs"
        ).is_dir():
            return parent
    return None


def resolve_cli_tool_provider_specs_and_dispatch_map(
    runtime_tools: Any,
) -> tuple[list[Any], dict[str, Any]]:
    from openminion.services.tool.exposure import (
        get_visible_tool_specs_and_dispatch_map,
    )

    return get_visible_tool_specs_and_dispatch_map(runtime_tools)


def _resolve_explicit_path(path_value: str | Path | None) -> Path | None:
    if path_value is None:
        return None
    raw = str(path_value or "").strip()
    if not raw:
        return None
    return Path(path_value).expanduser().resolve(strict=False)


def _config_path_arg(config_path: object | None) -> str | None:
    if isinstance(config_path, Path):
        return str(config_path)
    if isinstance(config_path, str):
        value = config_path.strip()
        return value or None
    return None


def load_cli_manager(
    config_path: object | None = None,
    *,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
) -> ConfigManager:
    from openminion.services.bootstrap.config import bootstrap_config_manager

    manager = ConfigManager.load(
        config_path,
        home_root=_resolve_explicit_path(home_root),
        data_root=_resolve_explicit_path(data_root),
    )
    bootstrap_config_manager(manager)
    return manager


def load_cli_config(
    config_path: object | None = None,
    *,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
) -> OpenMinionConfig:
    return load_cli_manager(
        config_path,
        home_root=home_root,
        data_root=data_root,
    ).base_config


def load_cli_config_with_path(
    config_path: object | None = None,
    *,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
) -> tuple[OpenMinionConfig, Path]:
    manager = load_cli_manager(
        config_path,
        home_root=home_root,
        data_root=data_root,
    )
    return manager.base_config, manager.config_path


def resolve_cli_roots(
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    runtime_env: Mapping[str, object] | None = None,
    process_env: Mapping[str, object] | None = None,
    config_path: object | None = None,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
    fallback_to_cwd: bool = True,
) -> CLIRoots:
    env_owner = resolve_environment_config(
        env=env,
        runtime_env=runtime_env,
        process_env=process_env,
    )
    resolved_home = _resolve_explicit_path(home_root)
    if resolved_home is None:
        resolved_home = resolve_home_root(
            config_path=_config_path_arg(config_path),
            fallback=str(Path.cwd()) if fallback_to_cwd else ".",
            env=env_owner,
        ).resolve(strict=False)

    raw_data_root = str(data_root or env_owner.openminion_data_root or "").strip()
    resolved_data_root = resolve_data_root(
        resolved_home,
        data_root=raw_data_root or None,
        env=env_owner,
    ).resolve(strict=False)
    return CLIRoots(
        env=env_owner,
        home_root=resolved_home,
        data_root=resolved_data_root,
    )


def _identity_config_value(config: Any, key: str) -> str:
    identity_cfg: Any = None
    if isinstance(config, dict):
        identity_cfg = config.get("identity")
    else:
        identity_cfg = getattr(config, "identity", None)
    if isinstance(identity_cfg, dict):
        return str(identity_cfg.get(key, "") or "").strip()
    if isinstance(identity_cfg, str):
        return identity_cfg.strip()
    return str(getattr(identity_cfg, key, "") or "").strip()


def resolve_identity_bundle_root(config: Any) -> str:
    bundle_root = _identity_config_value(config, "bundle_root")
    if bundle_root:
        return bundle_root
    return _identity_config_value(config, "root")


def resolve_identity_db_path(config: Any) -> str:
    db_path = _identity_config_value(config, "db_path")
    if db_path:
        return db_path
    return _identity_config_value(config, "root")


def resolve_cli_identity_db_path(
    config: Any | None = None,
    *,
    roots: CLIRoots | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    runtime_env: Mapping[str, object] | None = None,
    process_env: Mapping[str, object] | None = None,
    config_path: object | None = None,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
    fallback_to_cwd: bool = True,
) -> Path:
    configured = str(
        resolve_identity_db_path(config) if config is not None else ""
    ).strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    resolved_roots = roots or resolve_cli_roots(
        env=env,
        runtime_env=runtime_env,
        process_env=process_env,
        config_path=config_path,
        home_root=home_root,
        data_root=data_root,
        fallback_to_cwd=fallback_to_cwd,
    )
    return (
        resolved_roots.data_root / CLI_IDENTITY_SUBDIR / CLI_IDENTITY_DB_FILENAME
    ).resolve(strict=False)


def resolve_cli_policy_db_path(
    *,
    roots: CLIRoots | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    runtime_env: Mapping[str, object] | None = None,
    process_env: Mapping[str, object] | None = None,
    config_path: object | None = None,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
    fallback_to_cwd: bool = True,
) -> Path:
    resolved_roots = roots or resolve_cli_roots(
        env=env,
        runtime_env=runtime_env,
        process_env=process_env,
        config_path=config_path,
        home_root=home_root,
        data_root=data_root,
        fallback_to_cwd=fallback_to_cwd,
    )
    return (
        resolved_roots.data_root / CLI_POLICY_SUBDIR / CLI_POLICY_DB_FILENAME
    ).resolve(strict=False)


__all__ = [
    "CLIEnv",
    "CLIRoots",
    "infer_workspace_home_root",
    "load_cli_config",
    "load_cli_config_with_path",
    "load_cli_manager",
    "resolve_cli_tool_provider_specs_and_dispatch_map",
    "resolve_cli_identity_db_path",
    "resolve_cli_policy_db_path",
    "resolve_cli_roots",
    "resolve_identity_bundle_root",
    "resolve_identity_db_path",
]
