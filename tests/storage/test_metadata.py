from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openminion.modules.storage.migrations.errors import DbIdentityError
from openminion.modules.storage.migrations.metadata import (
    ensure_module_metadata_for_package,
    ensure_module_metadata_via_store,
)
from openminion.modules.storage.migrations.module_ids import get_module_application_id
from openminion.modules.storage.record_store import RecordStoreSQLite


def _create_db(path: Path, *, application_id: int = 0) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(f"PRAGMA application_id={int(application_id)}")
    conn.execute("PRAGMA user_version=0")
    conn.commit()
    return conn


def test_metadata_sets_application_id_for_uninitialized_db(tmp_path: Path) -> None:
    db_path = tmp_path / "uninitialized.db"
    conn = _create_db(db_path, application_id=0)
    try:
        meta = ensure_module_metadata_for_package(
            conn,
            package="openminion.modules.session.storage",
            migrations=["v1"],
        )
        app_id = conn.execute("PRAGMA application_id").fetchone()[0]
    finally:
        conn.close()

    assert app_id == get_module_application_id("session")
    assert meta.get("module_id") == "session"
    assert meta.get("schema_head") == "v1"


def test_metadata_rejects_application_id_mismatch(tmp_path: Path) -> None:
    db_path = tmp_path / "mismatch.db"
    conn = _create_db(db_path, application_id=0x4F4D00FF)
    try:
        with pytest.raises(DbIdentityError):
            ensure_module_metadata_for_package(
                conn,
                package="openminion.modules.session.storage",
                migrations=["v1"],
            )
        app_id = conn.execute("PRAGMA application_id").fetchone()[0]
    finally:
        conn.close()

    assert app_id == 0x4F4D00FF


def test_metadata_via_store_upserts_backend_neutral_rows(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "module.db")
    try:
        meta = ensure_module_metadata_via_store(
            store,
            module_id="secret",
            schema_head="0001_baseline",
        )
        assert meta["module_id"] == "secret"
        assert meta["schema_head"] == "0001_baseline"

        updated = ensure_module_metadata_via_store(
            store,
            module_id="secret",
            schema_head="0002_next",
        )
        assert updated["schema_head"] == "0002_next"
        rows = store.query_rows("om_meta", order="key")
        assert {row["key"] for row in rows} >= {
            "module_id",
            "schema_head",
            "last_migrated_at",
        }
    finally:
        store.close()
