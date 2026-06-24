from __future__ import annotations

from typing import Any


def resolve_cron_store(runtime):
    from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
    from openminion.modules.storage.runtime.sqlite import resolve_database_path
    from openminion.modules.brain.paths import resolve_brain_sessions_db_path

    storage_path = resolve_database_path(runtime.config.storage.path)
    db_path = resolve_brain_sessions_db_path(storage_path=storage_path)
    return SQLiteSessionStore(db_path)


def list_cron_jobs(*, runtime) -> list[dict[str, Any]]:
    store = resolve_cron_store(runtime)
    return store.list_cron_jobs()
