from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from openminion.modules.storage.migrations.meta_rows import rows_to_meta
from openminion.modules.storage.record_store import RecordStore
from openminion.modules.storage.migrations.errors import DbIdentityError
from openminion.modules.storage.migrations.module_ids import (
    get_module_application_id,
    module_id_from_package,
    schema_head_from_migrations,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _pragma_int(conn: sqlite3.Connection, pragma_name: str, *, default: int = 0) -> int:
    row = conn.execute(f"PRAGMA {pragma_name}").fetchone()
    if row is None or row[0] is None:
        return int(default)
    return int(row[0])


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def read_om_meta(conn: sqlite3.Connection) -> dict[str, str]:
    if not _table_exists(conn, "om_meta"):
        return {}
    rows = conn.execute("SELECT key, value FROM om_meta").fetchall()
    return rows_to_meta(rows)


def ensure_module_metadata(
    conn: sqlite3.Connection,
    *,
    module_id: str,
    module_application_id: int,
    schema_head: str | None,
    user_version: int | None = None,
) -> dict[str, str]:
    module_id = str(module_id).strip()
    if not module_id:
        raise ValueError("module_id is required")

    dirty = False
    current_app_id = _pragma_int(conn, "application_id", default=0)
    if current_app_id == 0:
        conn.execute(f"PRAGMA application_id={int(module_application_id)}")
        dirty = True
    elif current_app_id != int(module_application_id):
        raise DbIdentityError(
            f"application_id mismatch for module '{module_id}': expected {int(module_application_id)}, "
            f"found {current_app_id}"
        )

    if user_version is not None:
        current_version = _pragma_int(conn, "user_version", default=0)
        if current_version == 0:
            conn.execute(f"PRAGMA user_version={int(user_version)}")
            dirty = True

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS om_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    existing = read_om_meta(conn)
    updates: dict[str, str] = {}

    if existing.get("module_id") != module_id:
        updates["module_id"] = module_id
    if schema_head is not None and existing.get("schema_head") != str(schema_head):
        updates["schema_head"] = str(schema_head)
    if updates:
        updates["last_migrated_at"] = _utc_now_iso()

    if updates:
        for key, value in updates.items():
            conn.execute(
                """
                INSERT INTO om_meta(key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )
        dirty = True

    if dirty:
        conn.commit()

    return read_om_meta(conn)


def ensure_module_metadata_for_package(
    conn: sqlite3.Connection,
    *,
    package: str | None,
    migrations: list[str] | None,
    user_version: int | None = None,
) -> dict[str, str]:
    module_id = module_id_from_package(package)
    return ensure_module_metadata(
        conn,
        module_id=module_id,
        module_application_id=get_module_application_id(module_id),
        schema_head=schema_head_from_migrations(migrations),
        user_version=user_version,
    )


def ensure_module_metadata_via_store(
    record_store: RecordStore,
    *,
    module_id: str,
    schema_head: str | None,
) -> dict[str, str]:
    module_id = str(module_id).strip()
    if not module_id:
        raise ValueError("module_id is required")

    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS om_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    existing_rows = record_store.query_dicts("SELECT key, value FROM om_meta")
    existing = rows_to_meta((row["key"], row["value"]) for row in existing_rows)
    updates: dict[str, str] = {}
    if existing.get("module_id") != module_id:
        updates["module_id"] = module_id
    if schema_head is not None and existing.get("schema_head") != str(schema_head):
        updates["schema_head"] = str(schema_head)
    if updates:
        updates["last_migrated_at"] = _utc_now_iso()

    for key, value in updates.items():
        record_store.execute_count(
            """
            INSERT INTO om_meta(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )

    rows = record_store.query_dicts("SELECT key, value FROM om_meta")
    return rows_to_meta((row["key"], row["value"]) for row in rows)
