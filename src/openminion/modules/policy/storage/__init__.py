from pathlib import Path

from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig

from .base import PolicyStore
from .store import PostgresPolicyStore, SQLitePolicyStore


def build_policy_store(
    *,
    config: StorageEngineConfig,
    database_path: str | Path,
) -> PolicyStore:
    backend = str(config.record_backend).strip()
    raw_db_path = str(database_path).strip()
    if backend == "record.sqlite":
        return SQLitePolicyStore(
            raw_db_path if raw_db_path == ":memory:" else database_path
        )

    if backend != "record.postgres":
        raise ValueError(
            f"Unsupported policy record backend: {config.record_backend!r}"
        )

    engine = StorageEngine.from_config(config=config)
    return PostgresPolicyStore(record_store=engine.record_store)


__all__ = (
    "PolicyStore",
    "PostgresPolicyStore",
    "SQLitePolicyStore",
    "build_policy_store",
)
