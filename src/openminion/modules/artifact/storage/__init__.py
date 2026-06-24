from pathlib import Path

from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig

from .base import ArtifactIndex, BlobStore
from .fs_cas import FileSystemCASBlobStore
from .store import PostgresArtifactIndex, SQLiteArtifactIndex


def build_artifact_index(
    *,
    config: StorageEngineConfig,
    database_path: str | Path,
) -> ArtifactIndex:
    record_backend = str(config.record_backend).strip()
    raw_db_path = str(database_path).strip()
    if record_backend == "record.sqlite":
        sqlite_path = raw_db_path if raw_db_path == ":memory:" else database_path
        return SQLiteArtifactIndex(sqlite_path)

    if record_backend != "record.postgres":
        raise ValueError(f"Unsupported artifact record backend: {record_backend!r}")

    engine = StorageEngine.from_config(config=config)
    return PostgresArtifactIndex(record_store=engine.record_store)


__all__ = [
    "ArtifactIndex",
    "BlobStore",
    "FileSystemCASBlobStore",
    "PostgresArtifactIndex",
    "SQLiteArtifactIndex",
    "build_artifact_index",
]
