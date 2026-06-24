from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.a2a.storage import (
    PostgresAuditStore,
    SQLiteAuditStore,
    build_a2a_audit_store,
)
from openminion.modules.storage.engine import StorageEngineConfig
from tests.storage.postgres_test_utils import (
    build_postgres_storage_config,
    open_postgres_record_store,
)


def test_build_a2a_audit_store_returns_sqlite_store(tmp_path: Path) -> None:
    store = build_a2a_audit_store(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "audit.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        audit_root=tmp_path / "audit",
        retention_days=7,
    )
    try:
        assert isinstance(store, SQLiteAuditStore)
    finally:
        store.close()


@pytest.mark.postgres
def test_build_a2a_audit_store_returns_postgres_store(tmp_path: Path) -> None:
    with open_postgres_record_store("sfc_a2a_audit_factory") as (_store, schema_name):
        store = build_a2a_audit_store(
            config=build_postgres_storage_config(
                tmp_path=tmp_path,
                schema_name=schema_name,
                sqlite_name="audit.db",
            ),
            audit_root=tmp_path / "audit",
            retention_days=7,
        )
        try:
            assert isinstance(store, PostgresAuditStore)
        finally:
            store.close()
