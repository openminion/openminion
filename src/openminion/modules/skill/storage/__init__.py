from pathlib import Path

from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig

from openminion.modules.skill.storage.base import SkillStore
from openminion.modules.skill.storage.store import (
    PostgresSkillStore,
    SQLiteSkillStore,
)


def build_skill_store(
    *,
    config: StorageEngineConfig,
    database_path: str | Path,
) -> SkillStore:
    backend = str(config.record_backend).strip()
    raw_db_path = str(database_path).strip()
    if backend == "record.sqlite":
        return SQLiteSkillStore(
            raw_db_path if raw_db_path == ":memory:" else database_path
        )

    if backend != "record.postgres":
        raise ValueError(f"Unsupported skill record backend: {config.record_backend!r}")

    engine = StorageEngine.from_config(config=config)
    return PostgresSkillStore(record_store=engine.record_store)


__all__ = (
    "PostgresSkillStore",
    "SQLiteSkillStore",
    "SkillStore",
    "build_skill_store",
)
