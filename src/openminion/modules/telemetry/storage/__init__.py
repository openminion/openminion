from pathlib import Path

from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig

from .base import TelemetryStore
from .store import PostgresTelemetryStore, SQLiteTelemetryStore


def build_telemetry_store(
    *,
    config: StorageEngineConfig,
    database_path: str | Path,
) -> TelemetryStore:
    raw_db_path = str(database_path).strip()
    if str(config.record_backend).strip() == "record.sqlite":
        return SQLiteTelemetryStore(
            raw_db_path if raw_db_path == ":memory:" else database_path
        )

    if str(config.record_backend).strip() != "record.postgres":
        raise ValueError(
            f"Unsupported telemetry record backend: {config.record_backend!r}"
        )

    engine = StorageEngine.from_config(config=config)
    return PostgresTelemetryStore(record_store=engine.record_store)


__all__ = (
    "PostgresTelemetryStore",
    "SQLiteTelemetryStore",
    "TelemetryStore",
    "build_telemetry_store",
)
