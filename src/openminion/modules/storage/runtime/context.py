from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection
from typing import Any
from collections.abc import Mapping

from openminion.base.config.env import EnvironmentConfig
from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig
from openminion.modules.storage.record_store import RecordStore
from openminion.modules.storage.telemetry import StorageTelemetryHook
from openminion.modules.storage.runtime.idempotency_store import IdempotencyStore
from openminion.modules.storage.runtime.migrations import (
    MigrationResult,
    migrate_database,
    migrate_record_store,
)
from openminion.modules.storage.runtime.schema_drift import (
    RUNTIME_ONLY_TABLES,
    SchemaDriftReport,
    derive_expected_schema,
    detect_schema_drift,
)
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import resolve_database_path


logger = logging.getLogger(__name__)

# well-known typed identifier for the warning emitted when startup
# drift detection surfaces a non-empty SchemaDriftReport. Tests and operator
# log scrapers may grep for this string.
SCHEMA_DRIFT_WARNING_EVENT = "storage.schema_drift_detected"


@dataclass
class RuntimeStorageContext:
    sqlite_path: Path
    migration_result: MigrationResult
    record_store: RecordStore
    sessions: SessionStore
    idempotency: IdempotencyStore
    engine: StorageEngine | None = None
    schema_drift_report: SchemaDriftReport | None = None
    _closed: bool = False

    @property
    def connection(self) -> Connection:
        connection = getattr(self.record_store, "connection", None)
        if connection is None:
            raise RuntimeError("runtime record_store does not expose sqlite connection")
        return connection

    def close(self) -> None:
        if self._closed:
            return
        # engine.close() stops the pool-health emitter thread
        if self.engine is not None:
            self.engine.close()
        else:
            self.record_store.close()
        self._closed = True


def _maybe_check_schema_drift_sqlite(
    connection: Connection,
) -> SchemaDriftReport | None:
    """Run the SDX-01 structural diff against an active SQLite connection.

    Read-only: opens no transactions, mutates no rows. Returns the typed
    report or ``None`` if drift detection cannot be performed (defensive —
    we never want to fail startup for a diagnostic check).
    """

    try:
        expected = derive_expected_schema()
        return detect_schema_drift(
            expected,
            connection,
            ignore_extra_tables=RUNTIME_ONLY_TABLES,
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "schema_drift check failed; skipping warning emission",
            exc_info=True,
        )
        return None


def _emit_schema_drift_warning(report: SchemaDriftReport) -> None:
    """Emit a typed warning log line when drift is detected.

    Anti-LLM §1: severity is the binary ``has_drift`` flag from the typed
    report. There is no LLM judgment about "is this drift serious".
    """

    if not report.has_drift:
        return
    payload = report.as_dict()
    logger.warning(
        "%s: storage schema drift detected (expected_head=%s, observed_head=%s, findings=%d)",
        SCHEMA_DRIFT_WARNING_EVENT,
        payload["expected_head_version"],
        payload["observed_head_version"],
        len(payload["findings"]),
        extra={
            "event": SCHEMA_DRIFT_WARNING_EVENT,
            "schema_drift_report": payload,
        },
    )


def build_runtime_storage(
    sqlite_path: str | Path,
    *,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
    record_backend: str = "record.sqlite",
    record_backend_options: dict[str, Any] | None = None,
    telemetry_hook: StorageTelemetryHook | None = None,
    check_schema_drift_on_startup: bool = True,
) -> RuntimeStorageContext:
    resolved_path = resolve_database_path(sqlite_path, env=env)
    backend_id = str(record_backend or "record.sqlite").strip() or "record.sqlite"
    # Boot-time validation for non-SQLite backends
    if backend_id != "record.sqlite":
        from openminion.modules.storage.runtime.validation import (
            validate_storage_config,
        )

        _pg_url = str((record_backend_options or {}).get("url", ""))
        validate_storage_config(
            "postgres" if "postgres" in backend_id else backend_id,
            _pg_url,
            check_connection=True,
            check_migrations=True,
        )
    engine = StorageEngine.from_config(
        config=StorageEngineConfig(
            root_dir=resolved_path.parent,
            sqlite_path=resolved_path,
            fallback_root=resolved_path.parent,
            record_backend=backend_id,
            record_backend_options=dict(record_backend_options or {}),
        ),
        telemetry_hook=telemetry_hook,
    )
    if backend_id == "record.sqlite":
        migration_result = migrate_database(resolved_path)
    else:
        migration_result = migrate_record_store(
            engine.record_store,
            backend_type="postgres",
        )
    sessions = SessionStore(engine.record_store)
    idempotency = IdempotencyStore(engine.record_store)

    # read-only schema-drift check + warning on startup. The check
    # only runs for the SQLite backend today — the Postgres / other-backend
    # case is left to a future expansion of detect_schema_drift.
    schema_drift_report: SchemaDriftReport | None = None
    if check_schema_drift_on_startup and backend_id == "record.sqlite":
        sqlite_connection = getattr(engine.record_store, "connection", None)
        if sqlite_connection is not None:
            schema_drift_report = _maybe_check_schema_drift_sqlite(sqlite_connection)
            if schema_drift_report is not None:
                _emit_schema_drift_warning(schema_drift_report)

    return RuntimeStorageContext(
        sqlite_path=resolved_path,
        migration_result=migration_result,
        record_store=engine.record_store,
        sessions=sessions,
        idempotency=idempotency,
        engine=engine,
        schema_drift_report=schema_drift_report,
    )
