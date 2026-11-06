from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from openminion.modules.memory.runtime.remote_transport import (
    RemoteMemoryStore,
    RemoteMemoryTransport,
)
from openminion.modules.memory.storage.capabilities import BackendCapabilities
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.storage.migrations.postgres_schema import (
    select_postgres_module_schema,
)
from ..errors import InvalidArgumentError, MigrationRequiredError


@dataclass(frozen=True)
class ResolvedMemoryBackend:
    backend: str
    store: Any
    capabilities: BackendCapabilities


_DEFAULT_CAPABILITIES = BackendCapabilities()
_NON_TRANSACTIONAL_CAPABILITIES = replace(
    _DEFAULT_CAPABILITIES,
    supports_transactions=False,
)


def _read_nested_mapping(config: Any, key: str) -> dict[str, Any]:
    value = config.get(key) if isinstance(config, dict) else getattr(config, key, None)
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    return {
        item: getattr(value, item)
        for item in dir(value)
        if not item.startswith("_") and not callable(getattr(value, item, None))
    }


def _resolve_backend_name(config: Any) -> str:
    if isinstance(config, dict):
        store = config.get("store")
        if isinstance(store, dict) and store.get("backend"):
            return str(store.get("backend")).strip().lower()
        backend = config.get("backend")
        if backend:
            return str(backend).strip().lower()
    store_obj = getattr(config, "store", None)
    backend = getattr(store_obj, "backend", None)
    if backend:
        return str(backend).strip().lower()
    backend = getattr(config, "backend", None)
    if backend:
        return str(backend).strip().lower()
    return "sqlite"


def _resolve_postgres_url(config: Any) -> str:
    if isinstance(config, dict):
        store = config.get("store")
        if isinstance(store, dict):
            postgres = store.get("postgres")
            if isinstance(postgres, dict) and postgres.get("url"):
                return str(postgres.get("url")).strip()
            if store.get("postgres_url"):
                return str(store.get("postgres_url")).strip()
        postgres = config.get("postgres")
        if isinstance(postgres, dict) and postgres.get("url"):
            return str(postgres.get("url")).strip()
        if config.get("postgres_url"):
            return str(config.get("postgres_url")).strip()
        return ""
    store_obj = getattr(config, "store", None)
    postgres_obj = getattr(store_obj, "postgres", None)
    url = getattr(postgres_obj, "url", None)
    if url:
        return str(url).strip()
    store_url = getattr(store_obj, "postgres_url", None)
    if store_url:
        return str(store_url).strip()
    postgres_obj = getattr(config, "postgres", None)
    url = getattr(postgres_obj, "url", None)
    if url:
        return str(url).strip()
    url = getattr(config, "postgres_url", None)
    if url:
        return str(url).strip()
    return ""


def _sqlite_path_from_config(config: Any, *, default: Path) -> Path:
    if config is None:
        return default
    if isinstance(config, dict):
        store_cfg = config.get("store")
        store_path = (
            store_cfg.get("sqlite_path") if isinstance(store_cfg, dict) else None
        )
        return Path(str(store_path or config.get("sqlite_path") or default))
    sqlite_path = default
    sqlite_path_attr = getattr(config, "sqlite_path", None)
    if sqlite_path_attr:
        sqlite_path = Path(str(sqlite_path_attr))
    store_cfg = getattr(config, "store", None)
    if store_cfg is not None:
        sqlite_path_attr = getattr(store_cfg, "sqlite_path", None)
        if sqlite_path_attr:
            sqlite_path = Path(str(sqlite_path_attr))
    return sqlite_path


def _resolved_backend(
    backend: str,
    store: Any,
    *,
    capabilities: BackendCapabilities | None = None,
) -> ResolvedMemoryBackend:
    return ResolvedMemoryBackend(
        backend=backend,
        store=store,
        capabilities=capabilities
        or getattr(store, "capabilities", _DEFAULT_CAPABILITIES),
    )


def resolve_memory_backend(
    *,
    config: Any | None,
    db_path: Path,
    artifactctl: Any | None = None,
) -> ResolvedMemoryBackend:
    backend = _resolve_backend_name(config)
    if backend == "mock":
        store = InMemoryMemoryStore()
        return _resolved_backend("mock", store)

    if backend == "remote":
        # A nested ``remote`` mapping (top level, then ``store.remote``) is used
        # as a whole, as before. Without one, read the typed ``StoreConfig``
        # fields that ``load_config`` fills and validates, from ``config.store``
        # or from the store config itself, keeping their timeout and retries.
        remote_cfg = _read_nested_mapping(config, "remote")
        if not remote_cfg:
            store_cfg = _read_nested_mapping(config, "store")
            remote_cfg = _read_nested_mapping(store_cfg, "remote")
        typed_store = getattr(config, "store", None) or config
        endpoint = str(
            remote_cfg.get("endpoint", "")
            if remote_cfg
            else getattr(typed_store, "remote_endpoint", "") or ""
        ).strip()
        if not endpoint:
            raise InvalidArgumentError("remote backend requires remote.endpoint")
        if remote_cfg:
            timeout_seconds = float(remote_cfg.get("timeout_seconds", 5.0) or 5.0)
            max_retries = int(remote_cfg.get("max_retries", 1) or 1)
            auth_token = str(remote_cfg.get("auth_token", "") or "")
        else:
            timeout_seconds = float(getattr(typed_store, "remote_timeout_seconds", 5.0))
            max_retries = int(getattr(typed_store, "remote_max_retries", 1))
            auth_token = ""
        transport = RemoteMemoryTransport(
            endpoint=endpoint,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            auth_token=auth_token,
        )
        store = RemoteMemoryStore(transport)
        return _resolved_backend(
            "remote",
            store,
            capabilities=_NON_TRANSACTIONAL_CAPABILITIES,
        )

    if backend == "postgres":
        postgres_url = _resolve_postgres_url(config)
        if not postgres_url:
            raise InvalidArgumentError(
                "postgres backend requires postgres.url or postgres_url"
            )
        try:
            import sqlalchemy as sa
        except ImportError as exc:
            raise MigrationRequiredError(
                "sqlalchemy is required for memory.postgres; install openminion[postgres]",
            ) from exc
        # The memory module's own schema under the configured base schema (#446).
        selection = select_postgres_module_schema(postgres_url, "memory")
        engine = sa.create_engine(
            selection.url(postgres_url), future=True, pool_pre_ping=True
        )
        postgres_kwargs: dict[str, Any] = {}
        if artifactctl is not None:
            postgres_kwargs["artifactctl"] = artifactctl
        try:
            store = PostgresMemoryStore(
                engine,
                database_path=db_path,
                owns_engine=True,
                postgres_schema=selection,
                **postgres_kwargs,
            )
        except BaseException:
            engine.dispose()
            raise
        return _resolved_backend("postgres", store)

    sqlite_kwargs: dict[str, Any] = {}
    if artifactctl is not None:
        sqlite_kwargs["artifactctl"] = artifactctl
    store = SQLiteMemoryStore(
        _sqlite_path_from_config(config, default=db_path),
        **sqlite_kwargs,
    )
    return _resolved_backend("sqlite", store)


__all__ = [
    "ResolvedMemoryBackend",
    "resolve_memory_backend",
]
