from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openminion.base.version import OPENMINION_VERSION
from openminion.modules.storage.interfaces import (
    STORAGE_INTERFACE_VERSION,
    BackendDescriptor,
)
from openminion.modules.storage.record_store import RecordStore, RecordStoreSQLite
from .blob_store import BlobStore, BlobStoreFS
from .zvec import ZvecVectorStore

RecordBackendFactory = Callable[[dict[str, Any]], RecordStore]
BlobBackendFactory = Callable[[dict[str, Any]], BlobStore]
VectorBackendFactory = Callable[[dict[str, Any]], Any]


class BackendRegistry:
    """Registry for pluggable storage backends by plane."""

    def __init__(self) -> None:
        self._record_factories: dict[str, RecordBackendFactory] = {}
        self._blob_factories: dict[str, BlobBackendFactory] = {}
        self._vector_factories: dict[str, VectorBackendFactory] = {}

    def copy(self) -> BackendRegistry:
        clone = BackendRegistry()
        clone._record_factories = dict(self._record_factories)
        clone._blob_factories = dict(self._blob_factories)
        clone._vector_factories = dict(self._vector_factories)
        return clone

    def register_record(self, backend_id: str, factory: RecordBackendFactory) -> None:
        self._record_factories[_require_backend_id(backend_id, kind="record")] = factory

    def register_blob(self, backend_id: str, factory: BlobBackendFactory) -> None:
        self._blob_factories[_require_backend_id(backend_id, kind="blob")] = factory

    def register_vector(self, backend_id: str, factory: VectorBackendFactory) -> None:
        self._vector_factories[_require_backend_id(backend_id, kind="vector")] = factory

    def list_record_backends(self) -> list[str]:
        return sorted(self._record_factories.keys())

    def list_blob_backends(self) -> list[str]:
        return sorted(self._blob_factories.keys())

    def list_vector_backends(self) -> list[str]:
        return sorted(self._vector_factories.keys())

    def create_record(
        self, backend_id: str, options: dict[str, Any] | None = None
    ) -> RecordStore:
        return _create_backend(
            self._record_factories,
            backend_id,
            options,
            kind="record",
        )

    def create_blob(
        self, backend_id: str, options: dict[str, Any] | None = None
    ) -> BlobStore:
        return _create_backend(self._blob_factories, backend_id, options, kind="blob")

    def create_vector(
        self, backend_id: str, options: dict[str, Any] | None = None
    ) -> Any:
        return _create_backend(
            self._vector_factories,
            backend_id,
            options,
            kind="vector",
        )


class NoopVectorStore:
    """Reference vector backend used for contract wiring and startup validation."""

    contract_version = STORAGE_INTERFACE_VERSION

    def __init__(self) -> None:
        self._namespaces: set[str] = set()

    def upsert(
        self,
        vectors: list[list[float]],
        metadata: list[dict[str, Any]],
        ids: list[str],
        namespace: str | None = None,
    ) -> None:
        if namespace:
            self._namespaces.add(str(namespace))

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def delete(self, ids: list[str], namespace: str | None = None) -> bool:
        return True

    def list_namespaces(self) -> list[str]:
        return sorted(self._namespaces)

    def namespace_stats(self, namespace: str) -> dict[str, Any]:
        return {"namespace": namespace, "count": 0}

    def count(self, namespace: str | None = None) -> int:
        return 0

    def healthcheck(self) -> dict[str, Any]:
        return {"ok": True}

    def describe_backend(self) -> BackendDescriptor:
        return BackendDescriptor(
            backend_id="vector.noop",
            version=OPENMINION_VERSION,
            planes_supported={"vector"},
            capabilities={"vector_search": False, "noop": True},
            limits={"max_vectors": 0},
        )


def _require_backend_id(backend_id: str, *, kind: str) -> str:
    normalized = str(backend_id or "").strip()
    if not normalized:
        raise ValueError(f"{kind} backend_id is required")
    return normalized


def _create_backend(
    registry: dict[str, Callable[[dict[str, Any]], Any]],
    backend_id: str,
    options: dict[str, Any] | None,
    *,
    kind: str,
) -> Any:
    normalized = str(backend_id or "").strip()
    factory = registry.get(normalized)
    if factory is None:
        raise KeyError(f"unknown {kind} backend: {normalized}")
    return factory(dict(options or {}))


def _sqlite_record_factory(options: dict[str, Any]) -> RecordStore:
    sqlite_path = options.get("sqlite_path")
    if sqlite_path is None:
        raise ValueError("sqlite_path is required for record.sqlite backend")
    slow_threshold = options.get("slow_query_threshold_ms")
    return RecordStoreSQLite(
        sqlite_path,
        wal=bool(options.get("wal", True)),
        synchronous=str(options.get("synchronous", "NORMAL")),
        busy_timeout_ms=int(options.get("busy_timeout_ms", 5000)),
        autocheckpoint_pages=int(options.get("autocheckpoint_pages", 1000)),
        telemetry_hook=options.get("telemetry_hook"),
        slow_query_threshold_ms=(
            int(slow_threshold) if slow_threshold is not None else 500
        ),
    )


def _postgres_record_factory(options: dict[str, Any]) -> RecordStore:
    url = str(options.get("url", "")).strip()
    if not url:
        raise ValueError("url is required for record.postgres backend")
    from .postgres import RecordStorePostgres

    def _opt_int(key: str) -> int | None:
        value = options.get(key)
        return None if value is None else int(value)

    def _opt_float(key: str) -> float | None:
        value = options.get(key)
        return None if value is None else float(value)

    slow_threshold = options.get("slow_query_threshold_ms")
    return RecordStorePostgres(
        url,
        pool_recycle_seconds=_opt_int("pool_recycle_seconds"),
        pool_size=_opt_int("pool_size"),
        pool_max_overflow=_opt_int("pool_max_overflow"),
        pool_timeout_seconds=_opt_float("pool_timeout_seconds"),
        telemetry_hook=options.get("telemetry_hook"),
        slow_query_threshold_ms=(
            int(slow_threshold) if slow_threshold is not None else 500
        ),
    )


def _filesystem_blob_factory(options: dict[str, Any]) -> BlobStore:
    root_dir = options.get("root_dir")
    if root_dir is None:
        raise ValueError("root_dir is required for blob.fs backend")
    return BlobStoreFS(root_dir)


def _zvec_vector_factory(options: dict[str, Any]) -> Any:
    sqlite_path = options.get("sqlite_path")
    if sqlite_path is None:
        raise ValueError("sqlite_path is required for vector.zvec backend")
    dimension = options.get("dimension")
    return ZvecVectorStore(
        sqlite_path=sqlite_path,
        dimension=(None if dimension in {None, ""} else int(dimension)),
        metric=str(options.get("metric", "cosine")),
        wal=bool(options.get("wal", True)),
    )


def default_backend_registry() -> BackendRegistry:
    registry = BackendRegistry()
    registry.register_record("record.sqlite", _sqlite_record_factory)
    registry.register_record("record.postgres", _postgres_record_factory)
    registry.register_blob("blob.fs", _filesystem_blob_factory)
    registry.register_vector("vector.zvec", _zvec_vector_factory)
    registry.register_vector("vector.noop", lambda options: NoopVectorStore())
    return registry
