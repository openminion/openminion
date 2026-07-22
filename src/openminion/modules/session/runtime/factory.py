"""Session runtime store factory."""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from openminion.base.config.env import (
    EnvironmentConfig,
    resolve_environment_config_with_explicit_env,
)
from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig

from ..storage.base import SessionStore
from ..storage.store import (
    _ARTIFACTCTL_UNSET,
    _resolve_db_path,
    _resolve_session_storage_roots,
    PostgresSessionStore,
    SQLiteSessionStore,
)


def build_module_session_store(
    *,
    config: StorageEngineConfig,
    database_path: str | Path,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
    artifactctl: Any = _ARTIFACTCTL_UNSET,
) -> SessionStore:
    resolved_env = resolve_environment_config_with_explicit_env(env)
    raw_db_path = str(database_path).strip()
    is_memory = raw_db_path == ":memory:"
    resolved_db_path = (
        Path(":memory:") if is_memory else _resolve_db_path(database_path)
    )
    record_backend = str(config.record_backend).strip()
    if record_backend not in {"record.sqlite", "record.postgres"}:
        raise ValueError(
            f"Unsupported session record backend: {config.record_backend!r}"
        )

    roots_path = (
        (Path.cwd() / ".openminion-session-postgres").resolve()
        if is_memory
        else resolved_db_path
    )
    storage_root, fallback_root = _resolve_session_storage_roots(
        roots_path,
        env=resolved_env,
    )
    engine = StorageEngine.from_config(
        config=replace(
            config,
            root_dir=storage_root,
            sqlite_path=resolved_db_path,
            fallback_root=fallback_root,
            default_namespace="sessctl",
            record_backend_options=dict(config.record_backend_options),
            blob_backend_options=dict(config.blob_backend_options),
            vector_backend_options=dict(config.vector_backend_options),
        )
    )
    if record_backend == "record.sqlite":
        return SQLiteSessionStore(
            raw_db_path if is_memory else resolved_db_path,
            record_store=engine.record_store,
            hybrid_store=engine.hybrid_store,
            artifactctl=artifactctl,
            env=resolved_env,
        )

    return PostgresSessionStore(
        roots_path,
        record_store=engine.record_store,
        hybrid_store=engine.hybrid_store,
        artifactctl=artifactctl,
        env=resolved_env,
    )


__all__ = ["build_module_session_store"]
