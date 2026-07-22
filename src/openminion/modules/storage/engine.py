from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from openminion.modules.storage.backends.registry import (
    BackendRegistry,
    default_backend_registry,
)
from openminion.modules.storage.backends.blob_store import BlobStore
from openminion.modules.storage.backends.hybrid_store import HybridStore
from openminion.modules.storage.interfaces import ensure_interface_compatibility
from openminion.modules.storage.record_store import RecordStore
from openminion.modules.storage.config import (
    POOL_HEALTH_EMIT_INTERVAL_SECONDS_DEFAULT as _POOL_HEALTH_DEFAULT_INTERVAL,
)
from openminion.modules.storage.runtime.health_emitter import (
    PoolHealthEmitter,
)
from openminion.modules.storage.telemetry import (
    NoopStorageTelemetryHook,
    StorageTelemetryHook,
)
from openminion.modules.storage.io import normalize_namespace


def _resolve_sqlite_path(value: str | Path) -> Path:
    raw = str(value).strip()
    if raw == ":memory:":
        return Path(":memory:")
    return Path(value).expanduser().resolve(strict=False)


def _storage_engine_paths(config: StorageEngineConfig) -> tuple[Path, Path, Path]:
    root = Path(config.root_dir).expanduser().resolve(strict=False)
    sqlite = _resolve_sqlite_path(config.sqlite_path)
    fallback = (
        Path(config.fallback_root).expanduser().resolve(strict=False)
        if config.fallback_root
        else root
    )
    return root, sqlite, fallback


def _record_backend_options(
    config: StorageEngineConfig,
    *,
    sqlite: Path,
    telemetry_hook: StorageTelemetryHook,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "sqlite_path": sqlite,
        "wal": bool(config.wal),
        "synchronous": str(config.synchronous),
        "busy_timeout_ms": int(config.busy_timeout_ms),
        "autocheckpoint_pages": int(config.autocheckpoint_pages),
        "telemetry_hook": telemetry_hook,
        "slow_query_threshold_ms": int(config.slow_query_threshold_ms),
    }
    optional_keys = {
        "pool_recycle_seconds": config.pg_pool_recycle_seconds,
        "pool_size": config.pg_pool_size,
        "pool_max_overflow": config.pg_pool_max_overflow,
        "pool_timeout_seconds": config.pg_pool_timeout_seconds,
    }
    for key, value in optional_keys.items():
        if value is not None:
            options[key] = value
    options.update(config.record_backend_options)
    return options


def _blob_backend_options(config: StorageEngineConfig, *, root: Path) -> dict[str, Any]:
    options: dict[str, Any] = {"root_dir": root}
    options.update(config.blob_backend_options)
    return options


def _create_vector_store(
    config: StorageEngineConfig,
    *,
    registry: BackendRegistry,
    sqlite: Path,
) -> Any | None:
    if not config.vector_backend:
        return None
    vector_options = dict(config.vector_backend_options)
    vector_options.setdefault("sqlite_path", sqlite)
    vector_options.setdefault("wal", bool(config.wal))
    vector_store = registry.create_vector(config.vector_backend, vector_options)
    ensure_interface_compatibility(vector_store, interface="vector_store")
    return vector_store


def _effective_storage_engine_config(
    config: StorageEngineConfig,
    *,
    root: Path,
    sqlite: Path,
    fallback: Path,
    namespace: str | None,
) -> StorageEngineConfig:
    return StorageEngineConfig(
        root_dir=root,
        sqlite_path=sqlite,
        fallback_root=fallback,
        wal=bool(config.wal),
        synchronous=str(config.synchronous),
        busy_timeout_ms=int(config.busy_timeout_ms),
        autocheckpoint_pages=int(config.autocheckpoint_pages),
        default_namespace=namespace,
        record_backend=str(config.record_backend),
        blob_backend=str(config.blob_backend),
        vector_backend=None if config.vector_backend is None else str(config.vector_backend),
        record_backend_options=dict(config.record_backend_options),
        blob_backend_options=dict(config.blob_backend_options),
        vector_backend_options=dict(config.vector_backend_options),
        pg_pool_recycle_seconds=config.pg_pool_recycle_seconds,
        pg_pool_size=config.pg_pool_size,
        pg_pool_max_overflow=config.pg_pool_max_overflow,
        pg_pool_timeout_seconds=config.pg_pool_timeout_seconds,
        pool_health_emit_interval_seconds=config.pool_health_emit_interval_seconds,
        slow_query_threshold_ms=int(config.slow_query_threshold_ms),
    )


def _start_pool_health_emitter(
    config: StorageEngineConfig,
    *,
    record: RecordStore,
    telemetry_hook: StorageTelemetryHook,
) -> PoolHealthEmitter | None:
    if isinstance(telemetry_hook, NoopStorageTelemetryHook):
        return None
    try:
        probe = record.pool_health()
    except Exception:  # noqa: BLE001
        probe = None
    if probe is None:
        return None
    interval = (
        config.pool_health_emit_interval_seconds
        if config.pool_health_emit_interval_seconds is not None
        else _POOL_HEALTH_DEFAULT_INTERVAL
    )
    emitter = PoolHealthEmitter(
        record_store=record,
        hook=telemetry_hook,
        interval_seconds=interval,
    )
    emitter.start()
    return emitter


@dataclass(frozen=True)
class StorageEngineConfig:
    root_dir: Path
    sqlite_path: Path
    fallback_root: Path
    wal: bool = True
    synchronous: str = "NORMAL"
    busy_timeout_ms: int = 5000
    autocheckpoint_pages: int = 1000
    default_namespace: str | None = None
    record_backend: str = "record.sqlite"
    blob_backend: str = "blob.fs"
    vector_backend: str | None = None
    record_backend_options: dict[str, Any] = field(default_factory=dict)
    blob_backend_options: dict[str, Any] = field(default_factory=dict)
    vector_backend_options: dict[str, Any] = field(default_factory=dict)
    pg_pool_recycle_seconds: int | None = None
    pg_pool_size: int | None = None
    pg_pool_max_overflow: int | None = None
    pg_pool_timeout_seconds: float | None = None
    pool_health_emit_interval_seconds: float | None = None
    slow_query_threshold_ms: int = 500

    def __post_init__(self) -> None:
        for name in (
            "pg_pool_recycle_seconds",
            "pg_pool_size",
            "pg_pool_max_overflow",
            "pg_pool_timeout_seconds",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            if value < 0:
                raise ValueError(
                    f"{name} must be >= 0 (got {value!r}); use None to fall "
                    f"back to the SQLAlchemy default."
                )


class ModuleStorage:
    def __init__(
        self,
        *,
        hybrid_store: HybridStore,
        namespace: str | None,
        vector_store: Any | None = None,
    ) -> None:
        self._hybrid_store = hybrid_store
        self.namespace = namespace
        self._vector_store = vector_store

    def write_blob(self, *args: Any, **kwargs: Any):
        return self._hybrid_store.write_blob(*args, **kwargs)

    def write_event(self, event: dict[str, Any]):
        return self._hybrid_store.write_event(event, namespace=self.namespace)

    def write_row(self, table: str, row: dict[str, Any]):
        return self._hybrid_store.write_row(table, row, namespace=self.namespace)

    def reindex(
        self,
        *,
        from_fs: bool = True,
        since_ts: str | None = None,
        dry_run: bool = False,
        archive_replayed: bool = False,
        archive_root: str | Path | None = None,
    ):
        return self._hybrid_store.reindex(
            from_fs=from_fs,
            since_ts=since_ts,
            dry_run=dry_run,
            archive_replayed=archive_replayed,
            archive_root=archive_root,
            namespace=self.namespace,
        )

    def list_events(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self._hybrid_store.list_events(
            session_id, limit=limit, namespace=self.namespace
        )

    def status(self) -> dict[str, Any]:
        payload = self._hybrid_store.status()
        if self.namespace is not None:
            payload["namespace"] = self.namespace
        return payload

    def gc(self, policy: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._hybrid_store.gc(policy)

    def sql_execute(self, sql: str, params: Iterable[Any] | None = None) -> Any:
        return self._hybrid_store.record_store.execute_count(sql, params)

    def sql_executemany(self, sql: str, params: Iterable[Iterable[Any]]) -> Any:
        total = 0
        for row_params in params:
            total += self._hybrid_store.record_store.execute_count(sql, row_params)
        return total

    def sql_query(
        self,
        sql: str,
        params: Iterable[Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self._hybrid_store.record_store.query_dicts(sql, params)

    def vector_upsert(
        self,
        *,
        vectors: list[list[float]],
        metadata: list[dict[str, Any]],
        ids: list[str],
    ) -> None:
        self._require_vector_store().upsert(
            vectors=vectors, metadata=metadata, ids=ids, namespace=self.namespace
        )

    def vector_search(
        self,
        *,
        query_vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self._require_vector_store().search(
            query_vector=query_vector,
            top_k=top_k,
            filters=filters,
            namespace=self.namespace,
        )

    def vector_delete(self, *, ids: list[str]) -> bool:
        return bool(
            self._require_vector_store().delete(ids=ids, namespace=self.namespace)
        )

    def vector_count(self) -> int:
        if self._vector_store is None:
            return 0
        return int(self._vector_store.count(namespace=self.namespace))

    def _require_vector_store(self) -> Any:
        if self._vector_store is None:
            raise RuntimeError("vector backend is not configured")
        return self._vector_store


class StorageEngine:
    def __init__(
        self,
        *,
        config: StorageEngineConfig,
        blob_store: BlobStore,
        record_store: RecordStore,
        hybrid_store: HybridStore,
        vector_store: Any | None = None,
        health_emitter: PoolHealthEmitter | None = None,
    ) -> None:
        self.config = config
        self.blob_store = blob_store
        self.record_store = record_store
        self.hybrid_store = hybrid_store
        self.vector_store = vector_store
        self._pool_health_emitter = health_emitter

    @classmethod
    def from_config(
        cls,
        *,
        config: StorageEngineConfig,
        registry: BackendRegistry | None = None,
        telemetry_hook: StorageTelemetryHook | None = None,
    ) -> StorageEngine:
        root, sqlite, fallback = _storage_engine_paths(config)
        namespace = normalize_namespace(config.default_namespace)
        backend_registry = (
            registry.copy() if registry is not None else default_backend_registry()
        )
        effective_hook: StorageTelemetryHook = (
            telemetry_hook if telemetry_hook is not None else NoopStorageTelemetryHook()
        )

        record = backend_registry.create_record(
            config.record_backend,
            _record_backend_options(config, sqlite=sqlite, telemetry_hook=effective_hook),
        )
        blob = backend_registry.create_blob(
            config.blob_backend,
            _blob_backend_options(config, root=root),
        )
        ensure_interface_compatibility(record, interface="record_store")
        ensure_interface_compatibility(blob, interface="blob_store")
        vector_store = _create_vector_store(
            config,
            registry=backend_registry,
            sqlite=sqlite,
        )
        hybrid = HybridStore(
            record_store=record,
            blob_store=blob,
            fallback_root=fallback,
            default_namespace=namespace,
        )
        ensure_interface_compatibility(hybrid, interface="hybrid_store")

        return cls(
            config=_effective_storage_engine_config(
                config,
                root=root,
                sqlite=sqlite,
                fallback=fallback,
                namespace=namespace,
            ),
            blob_store=blob,
            record_store=record,
            hybrid_store=hybrid,
            vector_store=vector_store,
            health_emitter=_start_pool_health_emitter(
                config,
                record=record,
                telemetry_hook=effective_hook,
            ),
        )

    @classmethod
    def from_paths(
        cls,
        *,
        root_dir: str | Path,
        sqlite_path: str | Path,
        fallback_root: str | Path | None = None,
        wal: bool = True,
        synchronous: str = "NORMAL",
        busy_timeout_ms: int = 5000,
        autocheckpoint_pages: int = 1000,
        default_namespace: str | None = None,
        record_backend: str = "record.sqlite",
        blob_backend: str = "blob.fs",
        vector_backend: str | None = None,
        record_backend_options: dict[str, Any] | None = None,
        blob_backend_options: dict[str, Any] | None = None,
        vector_backend_options: dict[str, Any] | None = None,
        registry: BackendRegistry | None = None,
    ) -> StorageEngine:
        root = Path(root_dir).expanduser().resolve(strict=False)
        sqlite = _resolve_sqlite_path(sqlite_path)
        fallback = (
            Path(fallback_root).expanduser().resolve(strict=False)
            if fallback_root
            else root
        )
        namespace = normalize_namespace(default_namespace)
        config = StorageEngineConfig(
            root_dir=root,
            sqlite_path=sqlite,
            fallback_root=fallback,
            wal=wal,
            synchronous=synchronous,
            busy_timeout_ms=busy_timeout_ms,
            autocheckpoint_pages=autocheckpoint_pages,
            default_namespace=namespace,
            record_backend=record_backend,
            blob_backend=blob_backend,
            vector_backend=vector_backend,
            record_backend_options=dict(record_backend_options or {}),
            blob_backend_options=dict(blob_backend_options or {}),
            vector_backend_options=dict(vector_backend_options or {}),
        )
        return cls.from_config(config=config, registry=registry)

    def module(self, namespace: str | None = None) -> ModuleStorage:
        resolved = self._resolve_namespace(namespace)
        return ModuleStorage(
            hybrid_store=self.hybrid_store,
            namespace=resolved,
            vector_store=self.vector_store,
        )

    def write_blob(self, *args: Any, **kwargs: Any):
        return self.hybrid_store.write_blob(*args, **kwargs)

    def write_event(self, event: dict[str, Any], *, namespace: str | None = None):
        return self.hybrid_store.write_event(
            event, namespace=self._resolve_namespace(namespace)
        )

    def write_row(
        self, table: str, row: dict[str, Any], *, namespace: str | None = None
    ):
        return self.hybrid_store.write_row(
            table, row, namespace=self._resolve_namespace(namespace)
        )

    def reindex(
        self,
        *,
        from_fs: bool = True,
        since_ts: str | None = None,
        dry_run: bool = False,
        archive_replayed: bool = False,
        archive_root: str | Path | None = None,
        namespace: str | None = None,
    ):
        return self.hybrid_store.reindex(
            from_fs=from_fs,
            since_ts=since_ts,
            dry_run=dry_run,
            archive_replayed=archive_replayed,
            archive_root=archive_root,
            namespace=self._resolve_namespace(namespace),
        )

    def list_events(
        self, session_id: str, *, limit: int = 50, namespace: str | None = None
    ) -> list[dict[str, Any]]:
        return self.hybrid_store.list_events(
            session_id,
            limit=limit,
            namespace=self._resolve_namespace(namespace),
        )

    def status(self, *, namespace: str | None = None) -> dict[str, Any]:
        payload = self.hybrid_store.status()
        resolved = self._resolve_namespace(namespace)
        if resolved is not None:
            payload["namespace"] = resolved
        return payload

    def gc(self, policy: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.hybrid_store.gc(policy)

    def close(self) -> None:
        # stop the periodic emitter before closing backends so the
        # background thread doesn't observe a half-closed record store.
        emitter = self._pool_health_emitter
        if emitter is not None:
            try:
                emitter.stop()
            except Exception:
                pass
            self._pool_health_emitter = None
        closed_ids: set[int] = set()
        for backend in (self.vector_store, self.record_store):
            if backend is None:
                continue
            backend_id = id(backend)
            if backend_id in closed_ids:
                continue
            closed_ids.add(backend_id)
            close_fn = getattr(backend, "close", None)
            if callable(close_fn):
                close_fn()

    def _resolve_namespace(self, namespace: str | None) -> str | None:
        if namespace is None:
            return self.config.default_namespace
        return normalize_namespace(namespace)
