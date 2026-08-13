from __future__ import annotations

import tempfile

from openminion.modules.storage.engine import StorageEngine


def prepare_task_module_storage(storage_engine: StorageEngine) -> None:
    """Prepare task-module storage by applying the current schema migration."""
    from openminion.modules.storage.migrations.task_tables import migrate_v1_to_v2

    migrate_v1_to_v2(storage_engine.record_store)


def run_migration_test() -> None:
    """Run a lightweight local migration smoke check."""
    import os

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        tmp_db_path = tmp_db.name

    try:
        engine = StorageEngine.from_paths(root_dir="/tmp", sqlite_path=tmp_db_path)

        store = engine.record_store
        from openminion.modules.storage.migrations.task_tables import migrate_v1_to_v2

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

    except Exception:
        try:
            os.unlink(tmp_db_path)
        except OSError:
            pass
        raise


if __name__ == "__main__":
    run_migration_test()
