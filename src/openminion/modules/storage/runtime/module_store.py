from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from pathlib import Path

from openminion.modules.storage.record_store import RecordStore, RecordStoreSQLite
from openminion.modules.storage.migrations.metadata import (
    ensure_module_metadata_via_store,
    ensure_module_metadata_for_package,
)
from openminion.modules.storage.migrations.module_ids import (
    module_id_from_package,
    schema_head_from_migrations,
)


class BaseModuleStore(ABC):
    """Shared constructor for backend-neutral module stores."""

    def __init__(self, *, record_store: RecordStore) -> None:
        self._lock = getattr(self, "_lock", threading.RLock())
        self._record_store = record_store
        self._init_schema()
        ensure_module_metadata_via_store(
            self._record_store,
            module_id=module_id_from_package(self._module_package()),
            schema_head=schema_head_from_migrations(self._list_migrations()),
        )

    def close(self) -> None:
        with self._lock:
            close_fn = getattr(self._record_store, "close", None)
            if callable(close_fn):
                close_fn()

    @abstractmethod
    def _init_schema(self) -> None: ...

    @abstractmethod
    def _list_migrations(self) -> list[str]: ...

    @abstractmethod
    def _module_package(self) -> str: ...


class BaseModuleSQLiteStore(BaseModuleStore):
    """Shared constructor for SQLite-backed module stores."""

    def __init__(
        self,
        sqlite_path: str | Path | None = None,
        *,
        wal: bool = True,
        record_store: RecordStore | None = None,
    ) -> None:
        self._lock = threading.RLock()
        if record_store is None:
            if sqlite_path is None:
                raise ValueError(
                    "sqlite_path is required when record_store is not provided"
                )
            raw = str(sqlite_path).strip()
            if raw == ":memory:":
                self.sqlite_path = Path(":memory:")
                self._record_store: RecordStore = RecordStoreSQLite(":memory:", wal=wal)
            else:
                resolved = Path(sqlite_path).expanduser().resolve(strict=False)
                self.sqlite_path = resolved
                resolved.parent.mkdir(parents=True, exist_ok=True)
                self._record_store = RecordStoreSQLite(resolved, wal=wal)
        else:
            self._record_store = record_store
            sqlite_path_value = getattr(
                record_store, "sqlite_path", sqlite_path or ":memory:"
            )
            raw = str(sqlite_path_value).strip()
            self.sqlite_path = (
                Path(":memory:")
                if raw == ":memory:"
                else Path(sqlite_path_value).expanduser().resolve(strict=False)
            )
        self._conn = getattr(self._record_store, "connection", None)
        if self._conn is None:
            raise RuntimeError("record_store must expose sqlite connection")
        self._conn.execute("PRAGMA foreign_keys=ON")
        super().__init__(record_store=self._record_store)
        ensure_module_metadata_for_package(
            self._conn,
            package=self._module_package(),
            migrations=self._list_migrations(),
        )
