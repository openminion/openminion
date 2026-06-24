from pathlib import Path

from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig

from .base import CompressTelemetryStore
from .checkpoint_store import PostgresCheckpointStore, SQLiteCheckpointStore
from .store import (
    DroppedReasonRow,
    ExplainPayload,
    FailureRow,
    PostgresTelemetryStore,
    RunRow,
    SQLiteTelemetryStore,
    TelemetryStore,
)


def build_compress_telemetry_store(
    *,
    config: StorageEngineConfig,
    database_path: str | Path,
) -> CompressTelemetryStore:
    record_backend = str(config.record_backend).strip()
    raw_db_path = str(database_path).strip()
    if record_backend == "record.sqlite":
        return SQLiteTelemetryStore(
            raw_db_path if raw_db_path == ":memory:" else database_path
        )

    if record_backend != "record.postgres":
        raise ValueError(
            f"Unsupported compress record backend: {config.record_backend!r}"
        )

    engine = StorageEngine.from_config(config=config)
    return PostgresTelemetryStore(record_store=engine.record_store)


__all__ = [
    "CompressTelemetryStore",
    "DroppedReasonRow",
    "ExplainPayload",
    "FailureRow",
    "PostgresCheckpointStore",
    "PostgresTelemetryStore",
    "RunRow",
    "SQLiteCheckpointStore",
    "SQLiteTelemetryStore",
    "TelemetryStore",
    "build_compress_telemetry_store",
]
