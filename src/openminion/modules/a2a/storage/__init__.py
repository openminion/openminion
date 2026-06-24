from __future__ import annotations

from pathlib import Path

from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig

from .base import AuditStore, StateStore
from .memory import MemoryAuditStore, MemoryStateStore
from .audit_store import PostgresAuditStore, SQLiteAuditStore
from .archive import ARCHIVE_TABLE_NAME, ArchiveReport, PostgresAuditArchiveStore
from .store import PostgresStateStore, SQLiteStateStore


def build_a2a_state_store(
    *,
    config: StorageEngineConfig,
    database_path: str | Path,
) -> StateStore:
    backend = str(config.record_backend).strip()
    raw_db_path = str(database_path).strip()
    if backend == "record.sqlite":
        return SQLiteStateStore(
            raw_db_path if raw_db_path == ":memory:" else database_path
        )

    _require_postgres_backend(backend)
    engine = StorageEngine.from_config(config=config)
    return PostgresStateStore(record_store=engine.record_store)


def build_a2a_audit_store(
    *,
    config: StorageEngineConfig,
    audit_root: str | Path,
    retention_days: int = 14,
) -> AuditStore:
    backend = str(config.record_backend).strip()
    raw_audit_root = Path(audit_root).expanduser().resolve(strict=False)
    if backend == "record.sqlite":
        return SQLiteAuditStore(raw_audit_root, retention_days=retention_days)

    _require_postgres_backend(backend)
    try:
        from sqlalchemy import create_engine
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "SQLAlchemy is required for Postgres-backed a2a audit storage."
        ) from exc

    record_options = dict(config.record_backend_options)
    url = str(record_options.get("url", "") or "").strip()
    if not url:
        raise ValueError(
            "Postgres-backed a2a audit storage requires record backend URL"
        )
    engine = create_engine(url, future=True)
    return PostgresAuditStore(
        engine,
        retention_days=retention_days,
        database_path=raw_audit_root / "audit.db",
        owns_engine=True,
    )


def _require_postgres_backend(backend: str) -> None:
    if backend != "record.postgres":
        raise ValueError(f"Unsupported a2a record backend: {backend!r}")


__all__ = [
    "ARCHIVE_TABLE_NAME",
    "ArchiveReport",
    "AuditStore",
    "MemoryAuditStore",
    "MemoryStateStore",
    "PostgresAuditArchiveStore",
    "PostgresAuditStore",
    "PostgresStateStore",
    "SQLiteAuditStore",
    "SQLiteStateStore",
    "StateStore",
    "build_a2a_audit_store",
    "build_a2a_state_store",
]
