from pathlib import Path

from openminion.modules.storage.runtime.sqlite import resolve_database_path


def resolve_brain_state_root(*, storage_path: Path) -> Path:
    base = resolve_database_path(storage_path)
    if base.parent.name == "brain":
        return base.parent.resolve()
    return (base.parent / "brain").resolve()


def resolve_brain_sessions_db_path(*, storage_path: Path) -> Path:
    return (
        resolve_brain_state_root(storage_path=storage_path) / "sessions.db"
    ).resolve()


def resolve_brain_runtime_db_path(*, storage_path: Path) -> Path:
    return (resolve_brain_state_root(storage_path=storage_path) / "brain.db").resolve()


__all__ = [
    "resolve_brain_runtime_db_path",
    "resolve_brain_sessions_db_path",
    "resolve_brain_state_root",
]
