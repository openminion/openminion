import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.base.config.paths import resolve_data_root, resolve_home_root
from .constants import (
    MEMORY_CAPSULE_STRATEGY_DYNAMIC_TURN,
    MEMORY_CAPSULE_STRATEGY_FROZEN_SESSION,
    MEMORY_CAPSULE_STRATEGY_OFF,
    MEMORY_CAPSULE_STRATEGY_REFRESH_ON_WRITE,
)

_OPENMINION_PLUGIN_PATHS_ENV = "OPENMINION_PLUGIN_PATHS"
_DEFAULT_SERVICES_PLUGIN_SEARCH_RELATIVE_PATH = (
    Path("src") / "openminion" / "extensions" / "custom"
)
ServicesEnv = EnvironmentConfig


@dataclass(frozen=True)
class ServicesRoots:
    env: EnvironmentConfig
    home_root: Path
    data_root: Path


def normalize_memory_capsule_strategy(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"frozen", "frozen_session", "session", "snapshot"}:
        return MEMORY_CAPSULE_STRATEGY_FROZEN_SESSION
    if normalized in {"dynamic", "dynamic_turn", "turn", "per_turn"}:
        return MEMORY_CAPSULE_STRATEGY_DYNAMIC_TURN
    if normalized in {"refresh_on_write", "write"}:
        return MEMORY_CAPSULE_STRATEGY_REFRESH_ON_WRITE
    if normalized in {"off", "disabled", "none"}:
        return MEMORY_CAPSULE_STRATEGY_OFF
    return MEMORY_CAPSULE_STRATEGY_DYNAMIC_TURN


def resolve_services_env(
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    runtime_env: Mapping[str, object] | None = None,
    process_env: Mapping[str, object] | None = None,
) -> ServicesEnv:
    return resolve_environment_config(
        env=env,
        runtime_env=runtime_env,
        process_env=process_env,
    )


def resolve_services_roots(
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    runtime_env: Mapping[str, object] | None = None,
    process_env: Mapping[str, object] | None = None,
    config_path: str | Path | None = None,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
    fallback_to_cwd: bool = True,
) -> ServicesRoots:
    env_owner = resolve_services_env(
        env=env,
        runtime_env=runtime_env,
        process_env=process_env,
    )
    resolved_home = (
        Path(home_root).expanduser().resolve(strict=False)
        if home_root is not None
        else resolve_home_root(
            config_path=str(config_path) if config_path is not None else None,
            fallback=str(Path.cwd()) if fallback_to_cwd else ".",
            env=env_owner,
        ).resolve(strict=False)
    )
    raw_data_root = str(data_root or env_owner.openminion_data_root or "").strip()
    resolved_data_root = resolve_data_root(
        resolved_home,
        data_root=raw_data_root or None,
        env=env_owner,
    ).resolve(strict=False)
    return ServicesRoots(
        env=env_owner,
        home_root=resolved_home,
        data_root=resolved_data_root,
    )


def resolve_services_path(
    path_value: str | Path,
    *,
    roots: ServicesRoots | None = None,
    relative_to: str = "data_root",
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    runtime_env: Mapping[str, object] | None = None,
    process_env: Mapping[str, object] | None = None,
    config_path: str | Path | None = None,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
    fallback_to_cwd: bool = True,
) -> Path:
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve(strict=False)
    resolved_roots = roots or resolve_services_roots(
        env=env,
        runtime_env=runtime_env,
        process_env=process_env,
        config_path=config_path,
        home_root=home_root,
        data_root=data_root,
        fallback_to_cwd=fallback_to_cwd,
    )
    anchor = (
        resolved_roots.home_root
        if str(relative_to or "").strip().lower() == "home_root"
        else resolved_roots.data_root
    )
    return (anchor / candidate).resolve(strict=False)


def resolve_services_plugin_paths(
    override: Sequence[Path] | None = None,
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    runtime_env: Mapping[str, object] | None = None,
    process_env: Mapping[str, object] | None = None,
    home_root: str | Path | None = None,
) -> list[Path]:
    if override is not None:
        return [Path(item).expanduser().resolve(strict=False) for item in override]

    env_owner = resolve_services_env(
        env=env,
        runtime_env=runtime_env,
        process_env=process_env,
    )
    env_value = env_owner.get(_OPENMINION_PLUGIN_PATHS_ENV, "").strip()
    raw_paths = [item for item in env_value.split(os.pathsep)] if env_value else []
    if not raw_paths:
        base_root = (
            Path(home_root).expanduser().resolve(strict=False)
            if home_root is not None
            else Path.cwd().resolve(strict=False)
        )
        raw_paths = [str(base_root / _DEFAULT_SERVICES_PLUGIN_SEARCH_RELATIVE_PATH)]

    resolved: list[Path] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        candidate = str(raw_path or "").strip()
        if not candidate:
            continue
        path = Path(candidate).expanduser().resolve(strict=False)
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    return resolved


__all__ = [
    "ServicesEnv",
    "ServicesRoots",
    "normalize_memory_capsule_strategy",
    "resolve_services_env",
    "resolve_services_path",
    "resolve_services_plugin_paths",
    "resolve_services_roots",
]
