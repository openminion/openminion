from pathlib import Path

from .migrations import run_migrations
from .store import SQLiteAuthoredToolStore


def build_authored_tool_store(
    *,
    sqlite_path: str | Path,
    wal: bool = True,
) -> SQLiteAuthoredToolStore:
    run_migrations(sqlite_path)
    return SQLiteAuthoredToolStore(sqlite_path=sqlite_path, wal=wal)
