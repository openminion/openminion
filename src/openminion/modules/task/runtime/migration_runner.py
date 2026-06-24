from __future__ import annotations

import tempfile
from pathlib import Path

from openminion.modules.storage.engine import StorageEngine
from openminion.modules.storage.migrations.registry import (
    get_module_spec,
)
from openminion.modules.storage.migrations.module_ids import get_module_application_id
from openminion.modules.storage.record_store import RecordStore


def prepare_task_module_storage(storage_engine: StorageEngine) -> None:
    """Prepare task-module storage by applying the current schema migration."""
    task_module_spec = get_module_spec("task")
    if not task_module_spec:
        task_module_spec = type(
            "DynamicTaskModule",
            (),
            {
                "module_id": "task",
                "module_application_id": get_module_application_id("task"),
                "apply_migration": lambda self, store, old_v, new_v: (
                    _apply_task_module_migrations(store, old_v, new_v)
                ),
            },
        )()

    store = storage_engine.record_store
    from openminion.modules.storage.migrations.task_tables import migrate_v1_to_v2

    migrate_v1_to_v2(store)


def _apply_task_module_migrations(
    store: RecordStore, old_version: int, new_version: int
) -> None:
    """Apply task-module migrations from ``old_version`` to ``new_version``."""
    from .migrations.task_tables import migrate_v1_to_v2

    if old_version < 1 and new_version >= 2:
        migrate_v1_to_v2(store)


def run_migration_test() -> None:
    """Run a lightweight local migration smoke check."""
    import os
    from openminion.modules.storage.engine import StorageEngine

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        tmp_db_path = tmp_db.name

    try:
        Path(tmp_db_path)
        engine = StorageEngine.from_paths(root_dir="/tmp", sqlite_path=tmp_db_path)

        store = engine.record_store
        from .migrations.task_tables import migrate_v1_to_v2

        migrate_v1_to_v2(store)

        rows = store.query_dicts(
            "SELECT name FROM sqlite_master WHERE type='table'",
            None,
        )
        tables = [str(row["name"]) for row in rows]
        print(f"Created tables: {tables}")

        required_tables = {
            "tasks",
            "plans",
            "plan_steps",
            "pending_actions",
            "step_idempotency",
        }
        missing_tables = required_tables - set(tables)

        if missing_tables:
            print(f"❌ ERROR: Missing tables: {missing_tables}")
            return
        print(f"✅ SUCCESS: All required tables created: {required_tables}")

        engine.close()
        os.unlink(tmp_db_path)

    except Exception as e:
        try:
            os.unlink(tmp_db_path)
        except OSError:
            pass
        raise e


if __name__ == "__main__":
    run_migration_test()
