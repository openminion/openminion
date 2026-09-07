"""Canonical identity root and database path resolution."""

from collections.abc import Mapping
from pathlib import Path

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.base.config.paths import resolve_data_root, resolve_home_root
from openminion.base.constants import (
    OPENMINION_DATA_ROOT_ENV,
    OPENMINION_IDENTITY_DB_ENV,
    OPENMINION_IDENTITY_ROOT_ENV,
)

_IDENTITY_DIRNAME = "identity"
_IDENTITY_DB_FILENAME = "identity.db"


def _resolve_home_root(env: EnvironmentConfig, home_root: Path | None) -> Path:
    return home_root.expanduser().resolve() if home_root else resolve_home_root(env=env)


def resolve_identity_root_from_env(
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    runtime_env: Mapping[str, object] | None = None,
    process_env: Mapping[str, object] | None = None,
    home_root: Path | None = None,
    data_root: Path | None = None,
    configured_root: str | Path | None = None,
) -> Path:
    resolved_env = resolve_environment_config(
        env=env,
        runtime_env=runtime_env,
        process_env=process_env,
    )
    base_root = _resolve_home_root(resolved_env, home_root)
    resolved_data_root = data_root or resolve_data_root(
        base_root,
        data_root=resolved_env.get(OPENMINION_DATA_ROOT_ENV, ""),
    )
    raw_root = (
        resolved_env.get(OPENMINION_IDENTITY_ROOT_ENV, "").strip()
        or str(configured_root or "").strip()
    )
    if raw_root:
        candidate = Path(raw_root).expanduser()
        if not candidate.is_absolute():
            candidate = resolved_data_root / candidate
        return candidate.resolve()
    return (resolved_data_root / _IDENTITY_DIRNAME).resolve()


def resolve_identity_db_from_env(
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    runtime_env: Mapping[str, object] | None = None,
    process_env: Mapping[str, object] | None = None,
    home_root: Path | None = None,
    data_root: Path | None = None,
    configured_db: str | Path | None = None,
    configured_root: str | Path | None = None,
) -> Path:
    resolved_env = resolve_environment_config(
        env=env,
        runtime_env=runtime_env,
        process_env=process_env,
    )
    env_db = resolved_env.get(OPENMINION_IDENTITY_DB_ENV, "").strip()
    if env_db:
        candidate = Path(env_db).expanduser()
        if not candidate.is_absolute():
            identity_root = resolve_identity_root_from_env(
                env=resolved_env,
                home_root=home_root,
                data_root=data_root,
                configured_root=configured_root,
            )
            candidate = identity_root / candidate
        return candidate.resolve()
    raw_db = str(configured_db or "").strip()
    if raw_db:
        candidate = Path(raw_db).expanduser()
        if not candidate.is_absolute():
            base_root = _resolve_home_root(resolved_env, home_root)
            resolved_data_root = data_root or resolve_data_root(
                base_root,
                data_root=resolved_env.get(OPENMINION_DATA_ROOT_ENV, ""),
            )
            candidate = resolved_data_root / candidate
        return candidate.resolve()
    identity_root = resolve_identity_root_from_env(
        env=resolved_env,
        home_root=home_root,
        data_root=data_root,
        configured_root=configured_root,
    )
    return (identity_root / _IDENTITY_DB_FILENAME).resolve()


__all__ = ["resolve_identity_db_from_env", "resolve_identity_root_from_env"]
