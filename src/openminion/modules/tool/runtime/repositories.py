"""Tool runtime repository handles."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping

from openminion.base.config.env import resolve_environment_config

from .audit import (
    ToolRuntimeAuditSink,
    audit_writes_storage,
    resolve_tool_runtime_audit_mode,
)
from .environment import identity_db_candidates


__all__ = [
    "LazyRepositoryHandle",
    "RuntimeRepositories",
    "build_runtime_repositories",
]


@dataclass
class LazyRepositoryHandle:
    """Thread-safe lazy repository initializer."""

    _factory: Callable[[], Any] | None = None
    _value: Any = None
    _initialized: bool = False
    _lock: RLock = field(default_factory=RLock)

    def get(self) -> Any:
        if self._initialized:
            return self._value
        with self._lock:
            if not self._initialized:
                self._value = self._factory() if callable(self._factory) else None
                self._initialized = True
        return self._value


@dataclass
class RuntimeRepositories:
    """Repository handle surface injected into RuntimeContext."""

    identity: LazyRepositoryHandle = field(default_factory=LazyRepositoryHandle)
    cron: LazyRepositoryHandle = field(default_factory=LazyRepositoryHandle)
    audit: LazyRepositoryHandle = field(default_factory=LazyRepositoryHandle)
    identity_path: Path | None = None
    cron_db_path: Path | None = None
    audit_db_path: Path | None = None


def build_runtime_repositories(
    *,
    context_metadata: Mapping[str, Any] | None,
) -> RuntimeRepositories:
    """Build repository handles for tool runtime contexts."""
    metadata = dict(context_metadata) if isinstance(context_metadata, Mapping) else {}
    runtime_env_payload: Mapping[str, object] | None = None
    raw_runtime_env = metadata.get("runtime_env")
    if isinstance(raw_runtime_env, Mapping):
        runtime_env_payload = raw_runtime_env
    elif isinstance(raw_runtime_env, str):
        try:
            parsed_runtime_env = json.loads(raw_runtime_env)
        except json.JSONDecodeError:
            parsed_runtime_env = None
        if isinstance(parsed_runtime_env, Mapping):
            runtime_env_payload = parsed_runtime_env
    env_owner = resolve_environment_config(runtime_env=runtime_env_payload)

    prewired = metadata.get("runtime_repositories")
    if isinstance(prewired, Mapping):
        identity_repo = prewired.get("identity")
        cron_repo = prewired.get("cron")
        audit_repo = prewired.get("audit")
        return RuntimeRepositories(
            identity=LazyRepositoryHandle(
                _factory=(lambda: identity_repo) if identity_repo is not None else None
            ),
            cron=LazyRepositoryHandle(
                _factory=(lambda: cron_repo) if cron_repo is not None else None
            ),
            audit=LazyRepositoryHandle(
                _factory=(lambda: audit_repo) if audit_repo is not None else None
            ),
        )

    identity_path = identity_db_candidates(env=env_owner)
    resolved_identity_path = identity_path[0] if identity_path else None

    storage_hint = str(metadata.get("storage_path", "") or "").strip() or None
    resolved_cron_db_path: Path | None = None
    resolved_audit_db_path: Path | None = None
    audit_mode = resolve_tool_runtime_audit_mode(
        context_metadata=metadata,
        env=env_owner,
    )
    try:
        from openminion.modules.brain.paths import resolve_brain_sessions_db_path
        from openminion.modules.storage.runtime.sqlite import resolve_database_path

        storage_path = resolve_database_path(storage_hint)
        if audit_writes_storage(audit_mode):
            resolved_audit_db_path = storage_path
        resolved_cron_db_path = resolve_brain_sessions_db_path(
            storage_path=storage_path
        )
    except Exception:
        resolved_cron_db_path = None
        resolved_audit_db_path = None

    def _build_identity_repo() -> Any:
        if resolved_identity_path is None:
            return None
        try:
            from openminion.modules.identity.storage.repository import (
                create_sqlite_identity_repository,
            )

            return create_sqlite_identity_repository(sqlite_path=resolved_identity_path)
        except Exception:
            return None

    def _build_cron_repo() -> Any:
        if resolved_cron_db_path is None:
            return None
        try:
            from openminion.modules.session.storage.repository import (
                create_sqlite_cron_repository,
            )

            return create_sqlite_cron_repository(db_path=resolved_cron_db_path)
        except Exception:
            return None

    def _build_audit_repo() -> Any:
        if resolved_audit_db_path is None:
            return None
        try:
            return ToolRuntimeAuditSink(db_path=resolved_audit_db_path)
        except Exception:
            return None

    return RuntimeRepositories(
        identity=LazyRepositoryHandle(_factory=_build_identity_repo),
        cron=LazyRepositoryHandle(_factory=_build_cron_repo),
        audit=LazyRepositoryHandle(_factory=_build_audit_repo),
        identity_path=resolved_identity_path,
        cron_db_path=resolved_cron_db_path,
        audit_db_path=resolved_audit_db_path,
    )
