from __future__ import annotations

from typing import Any

import pytest

from openminion.modules.storage.interfaces import ensure_interface_compatibility
from openminion.modules.storage.record_store import RecordStore, RecordStoreSQLite
from tests.storage.conftest import BackendCase


class NoRawSqlRecordStore(RecordStore):
    contract_version = "v1"

    def __init__(self, delegate: RecordStoreSQLite) -> None:
        self._delegate = delegate

    def begin(self) -> None:
        self._delegate.begin()

    def commit(self) -> None:
        self._delegate.commit()

    def rollback(self) -> None:
        self._delegate.rollback()

    def execute(self, sql: str, params=None) -> Any:
        raise NotImplementedError("raw_sql disabled")

    def executemany(self, sql: str, params) -> Any:
        raise NotImplementedError("raw_sql disabled")

    def query(self, sql: str, params=None) -> list[Any]:
        raise NotImplementedError("raw_sql disabled")

    def query_dicts(self, sql: str, params=None) -> list[dict[str, Any]]:
        return self._delegate.query_dicts(sql, params)

    def execute_count(self, sql: str, params=None) -> int:
        return self._delegate.execute_count(sql, params)

    def insert(self, table: str, row: dict[str, Any]) -> int:
        return self._delegate.insert(table, row)

    def query_rows(
        self,
        table: str,
        where: dict[str, Any] | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self._delegate.query_rows(table, where=where, order=order, limit=limit)

    def update_rows(
        self, table: str, where: dict[str, Any], values: dict[str, Any]
    ) -> int:
        return self._delegate.update_rows(table, where=where, values=values)

    def delete_rows(self, table: str, where: dict[str, Any]) -> int:
        return self._delegate.delete_rows(table, where=where)

    def healthcheck(self) -> dict[str, Any]:
        return self._delegate.healthcheck()

    def migrate(self, schema_version: int) -> None:
        self._delegate.migrate(schema_version)

    @property
    def in_transaction(self) -> bool:
        return self._delegate.in_transaction

    def last_error(self) -> str | None:
        return self._delegate.last_error()

    def diagnostics(self) -> dict[str, Any]:
        return self._delegate.diagnostics()

    def capabilities(self) -> dict[str, bool]:
        return {"checkpoint": False, "raw_sql": False, "wal": False}

    def close(self) -> None:
        self._delegate.close()


@pytest.fixture(params=["sqlite", "no-raw-sql"], ids=["sqlite", "no-raw-sql"])
def raw_sql_backend_case(tmp_path, request):
    if request.param == "sqlite":
        store = RecordStoreSQLite(tmp_path / "raw-sql.db")
        try:
            yield ("sqlite", store)
        finally:
            store.close()
        return

    store = NoRawSqlRecordStore(RecordStoreSQLite(tmp_path / "raw-sql-disabled.db"))
    try:
        yield ("no-raw-sql", store)
    finally:
        store.close()


def _bootstrap_items_table(store: RecordStore) -> None:
    store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            value INTEGER NOT NULL
        )
        """
    )


def test_backend_interface_conformance(record_store_case: BackendCase) -> None:
    ensure_interface_compatibility(record_store_case.store, interface="record_store")
    assert record_store_case.store.contract_version == "v1"


def test_backend_lifecycle_begin_commit_and_rollback(
    record_store_case: BackendCase,
) -> None:
    store = record_store_case.store
    _bootstrap_items_table(store)

    store.begin()
    assert store.in_transaction is True
    store.insert("items", {"id": 1, "name": "alpha", "value": 10})
    store.rollback()
    assert store.in_transaction is False
    assert store.query_rows("items", where={"id": 1}) == []

    store.begin()
    store.insert("items", {"id": 2, "name": "beta", "value": 20})
    store.commit()
    assert store.in_transaction is False
    assert store.query_rows("items", where={"id": 2}) == [
        {"id": 2, "name": "beta", "value": 20}
    ]


def test_backend_transaction_context_manager(record_store_case: BackendCase) -> None:
    store = record_store_case.store
    _bootstrap_items_table(store)

    with store.transaction():
        store.insert("items", {"id": 11, "name": "inside", "value": 1})
    assert store.query_rows("items", where={"id": 11}, limit=1)[0]["name"] == "inside"

    with pytest.raises(RuntimeError):
        with store.transaction():
            store.insert("items", {"id": 12, "name": "rollback", "value": 2})
            raise RuntimeError("rollback")
    assert store.query_rows("items", where={"id": 12}) == []


def test_backend_neutral_crud_methods(record_store_case: BackendCase) -> None:
    store = record_store_case.store
    _bootstrap_items_table(store)

    inserted_id = store.insert("items", {"id": 21, "name": "alpha", "value": 3})
    assert inserted_id in {0, 21}
    assert store.query_rows("items", where={"id": 21}, limit=1) == [
        {"id": 21, "name": "alpha", "value": 3}
    ]
    assert store.query_dicts("SELECT id, name, value FROM items ORDER BY id") == [
        {"id": 21, "name": "alpha", "value": 3}
    ]
    assert store.update_rows("items", where={"id": 21}, values={"value": 4}) == 1
    assert store.query_rows("items", where={"id": 21}, limit=1)[0]["value"] == 4
    assert (
        store.execute_count(
            "INSERT INTO items (id, name, value) VALUES (?, ?, ?)",
            (22, "beta", 8),
        )
        == 1
    )
    assert store.delete_rows("items", where={"id": 22}) == 1
    assert store.query_rows("items", where={"id": 22}) == []


def test_backend_capabilities_and_checkpoint(record_store_case: BackendCase) -> None:
    store = record_store_case.store
    capabilities = store.capabilities()
    assert set(capabilities) == {"checkpoint", "raw_sql", "wal"}
    if capabilities["checkpoint"]:
        checkpoint = store.checkpoint()
        assert isinstance(checkpoint, tuple)
        assert len(checkpoint) == 3
    else:
        assert store.checkpoint() == (0, 0, 0)


def test_backend_health_diagnostics_and_last_error(
    record_store_case: BackendCase,
) -> None:
    store = record_store_case.store
    _bootstrap_items_table(store)
    health = store.healthcheck()
    assert health["ok"] is True
    diagnostics = store.diagnostics()
    assert isinstance(diagnostics, dict)
    assert store.last_error() is None

    with pytest.raises(Exception):  # noqa: BLE001
        store.query_rows("missing_table")
    assert store.last_error() is not None


def test_raw_sql_methods_are_capability_gated(raw_sql_backend_case) -> None:
    backend_name, store = raw_sql_backend_case
    _bootstrap_items_table(store)
    if not store.capabilities()["raw_sql"]:
        pytest.skip(f"raw_sql disabled for {backend_name}")

    with pytest.deprecated_call(match=r"RecordStoreSQLite\.execute\(\) is deprecated"):
        cursor = store.execute(
            "INSERT INTO items (id, name, value) VALUES (?, ?, ?)",
            (31, "raw", 1),
        )
    assert cursor is not None
    with pytest.deprecated_call(
        match=r"RecordStoreSQLite\.executemany\(\) is deprecated"
    ):
        store.executemany(
            "INSERT INTO items (id, name, value) VALUES (?, ?, ?)",
            [(32, "raw2", 2)],
        )
    with pytest.deprecated_call(match=r"RecordStoreSQLite\.query\(\) is deprecated"):
        rows = store.query("SELECT id, name, value FROM items ORDER BY id")
    assert len(rows) >= 2
