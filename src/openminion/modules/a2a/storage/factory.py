from __future__ import annotations

from pathlib import Path

from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig
from openminion.modules.storage.migrations.postgres_schema import (
    select_postgres_module_schema,
)

from .audit_store import PostgresAuditStore, SQLiteAuditStore
from .base import AuditStore, StateStore
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
    engine = StorageEngine.from_config(config=config, module_id="a2a")
    return PostgresStateStore(record_store=engine.record_store)


def build_a2a_audit_store(
    *,
    config: StorageEngineConfig,
    audit_root: str | Path,
    capture_payloads: bool = False,
    retention_days: int = 14,
    archive_retention_days: int = 0,
) -> AuditStore:
    backend = str(config.record_backend).strip()
    raw_audit_root = Path(audit_root).expanduser().resolve(strict=False)
    if backend == "record.sqlite":
        return SQLiteAuditStore(
            raw_audit_root,
            capture_payloads=capture_payloads,
            retention_days=retention_days,
            archive_retention_days=archive_retention_days,
        )

    _require_postgres_backend(backend)
    try:
        from sqlalchemy import create_engine
    except ImportError as exc:
        raise RuntimeError(
            "SQLAlchemy is required for Postgres-backed a2a audit storage."
        ) from exc

    record_options = dict(config.record_backend_options)
    url = str(record_options.get("url", "") or "").strip()
    if not url:
        raise ValueError(
            "Postgres-backed a2a audit storage requires record backend URL"
        )
    # The a2a module's own schema under the configured base schema (#446).
    selection = select_postgres_module_schema(url, "a2a")
    engine = create_engine(selection.url(url), future=True)
    try:
        return PostgresAuditStore(
            engine,
            capture_payloads=capture_payloads,
            retention_days=retention_days,
            archive_retention_days=archive_retention_days,
            database_path=raw_audit_root / "audit.db",
            owns_engine=True,
            postgres_schema=selection,
        )
    except BaseException:
        engine.dispose()
        raise


def _require_postgres_backend(backend: str) -> None:
    if backend != "record.postgres":
        raise ValueError(f"Unsupported a2a record backend: {backend!r}")
