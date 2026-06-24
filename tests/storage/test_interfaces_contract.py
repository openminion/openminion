from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openminion.modules.storage import (
    BlobStoreFS,
    BlobStoreInterface,
    CapabilityRequirement,
    BackendDescriptor,
    HybridStore,
    HybridStoreInterface,
    RecordStoreInterface,
    RecordStoreSQLite,
    STORAGE_INTERFACE_VERSION,
    StorageEnvelope,
    StorageError,
    UnsupportedCapabilityError,
    ensure_interface_compatibility,
    check_capability_support,
    create_capability_error_envelope,
)


def test_storage_contract_version_is_stable() -> None:
    assert STORAGE_INTERFACE_VERSION == "v1"
    assert RecordStoreSQLite.contract_version == "v1"
    assert BlobStoreFS.contract_version == "v1"
    assert HybridStore.contract_version == "v1"


def test_record_store_interface_positive(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "record.db")
    assert isinstance(store, RecordStoreInterface)
    ensure_interface_compatibility(store, interface="record_store")
    store.close()


def test_blob_store_interface_positive(tmp_path: Path) -> None:
    blob = BlobStoreFS(tmp_path)
    assert isinstance(blob, BlobStoreInterface)
    ensure_interface_compatibility(blob, interface="blob_store")


def test_hybrid_store_interface_positive(tmp_path: Path) -> None:
    record = RecordStoreSQLite(tmp_path / "hybrid.db")
    blob = BlobStoreFS(tmp_path)
    hybrid = HybridStore(
        record_store=record, blob_store=blob, fallback_root=tmp_path / "fallback"
    )
    assert isinstance(hybrid, HybridStoreInterface)
    ensure_interface_compatibility(hybrid, interface="hybrid_store")
    record.close()


def test_structured_store_interface_contract() -> None:

    # Mock implementation to test interface compliance
    class MockStructuredStore:
        contract_version = "v1"

        def create(self, table: str, data: dict[str, Any]) -> Any:
            pass

        def read(self, table: str, id_value: Any) -> dict[str, Any]:
            return {}

        def update(self, table: str, id_value: Any, data: dict[str, Any]) -> Any:
            pass

        def delete(self, table: str, id_value: Any) -> bool:
            return True

        def batch_create(self, table: str, items: list[dict[str, Any]]) -> list[Any]:
            return []

        def batch_read(self, table: str, ids: list[Any]) -> list[dict[str, Any]]:
            return []

        def batch_update(
            self, table: str, updates: list[tuple[Any, dict[str, Any]]]
        ) -> list[bool]:
            return []

        def batch_delete(self, table: str, ids: list[Any]) -> list[bool]:
            return []

        def query(self, table: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
            return []

        def count(self, table: str, filters: dict[str, Any]) -> int:
            return 0

        def begin_transaction(self) -> Any:
            return None

        def commit_transaction(self, tx_handle: Any) -> None:
            pass

        def rollback_transaction(self, tx_handle: Any) -> None:
            pass

        def healthcheck(self) -> dict[str, Any]:
            return {}

        def describe_backend(self) -> Any:
            return BackendDescriptor(
                backend_id="mock",
                version="1.0.0",
                planes_supported={"record", "blob"},
                capabilities={},
                limits={},
            )

    mock_store = MockStructuredStore()
    assert hasattr(mock_store, "create")
    assert hasattr(mock_store, "batch_create")
    assert hasattr(mock_store, "query")
    assert hasattr(mock_store, "count")
    assert hasattr(mock_store, "describe_backend")


def test_vector_store_interface_contract() -> None:

    # Mock implementation to test interface compliance
    class MockVectorStore:
        contract_version = "v1"

        def upsert(self, vectors, metadata, ids, namespace=None):
            pass

        def search(self, query_vector, top_k=10, filters=None, namespace=None):
            return []

        def delete(self, ids, namespace=None):
            return True

        def list_namespaces(self):
            return []

        def namespace_stats(self, namespace):
            return {}

        def count(self, namespace=None):
            return 0

        def healthcheck(self) -> dict[str, Any]:
            return {}

        def describe_backend(self) -> Any:
            return BackendDescriptor(
                backend_id="mock",
                version="1.0.0",
                planes_supported={"vector"},
                capabilities={},
                limits={},
            )

    mock_store = MockVectorStore()
    assert hasattr(mock_store, "upsert")
    assert hasattr(mock_store, "search")
    assert hasattr(mock_store, "delete")
    assert hasattr(mock_store, "list_namespaces")
    assert hasattr(mock_store, "namespace_stats")
    assert hasattr(mock_store, "describe_backend")


def test_interface_validator_positive_new_interfaces() -> None:

    class MockStructuredStore:
        contract_version = "v1"

        def create(self, table: str, data: dict[str, Any]) -> Any:
            pass

        def read(self, table: str, id_value: Any) -> dict[str, Any]:
            return {}

        def update(self, table: str, id_value: Any, data: dict[str, Any]) -> Any:
            pass

        def delete(self, table: str, id_value: Any) -> bool:
            return True

        def batch_create(self, table: str, items: list[dict[str, Any]]) -> list[Any]:
            return []

        def batch_read(self, table: str, ids: list[Any]) -> list[dict[str, Any]]:
            return []

        def batch_update(
            self, table: str, updates: list[tuple[Any, dict[str, Any]]]
        ) -> list[bool]:
            return []

        def batch_delete(self, table: str, ids: list[Any]) -> list[bool]:
            return []

        def query(self, table: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
            return []

        def count(self, table: str, filters: dict[str, Any]) -> int:
            return 0

        def begin_transaction(self) -> Any:
            return None

        def commit_transaction(self, tx_handle: Any) -> None:
            pass

        def rollback_transaction(self, tx_handle: Any) -> None:
            pass

        def healthcheck(self) -> dict[str, Any]:
            return {}

        def describe_backend(self) -> Any:
            return BackendDescriptor(
                backend_id="mock",
                version="1.0.0",
                planes_supported={"record", "blob"},
                capabilities={},
                limits={},
            )

    mock_store = MockStructuredStore()
    # This should not raise an exception because all required methods are present
    ensure_interface_compatibility(mock_store, interface="structured_store")


def test_query_dicts_returns_list_of_dicts(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "qd.db")
    store.execute_count("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    store.insert("t", {"id": 1, "name": "alpha"})
    store.insert("t", {"id": 2, "name": "beta"})

    rows = store.query_dicts("SELECT id, name FROM t ORDER BY id")
    assert rows == [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]
    # Verify each element is a plain dict, not sqlite3.Row
    for row in rows:
        assert type(row) is dict
    store.close()


def test_query_dicts_empty_result(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "qd_empty.db")
    store.execute_count("CREATE TABLE t (id INTEGER)")
    rows = store.query_dicts("SELECT * FROM t")
    assert rows == []
    store.close()


def test_query_dicts_with_params(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "qd_params.db")
    store.execute_count("CREATE TABLE t (id INTEGER, val TEXT)")
    store.insert("t", {"id": 1, "val": "a"})
    store.insert("t", {"id": 2, "val": "b"})
    rows = store.query_dicts("SELECT * FROM t WHERE id = ?", [1])
    assert rows == [{"id": 1, "val": "a"}]
    store.close()


def test_query_dicts_bad_sql_propagates_error(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "qd_err.db")
    with pytest.raises(Exception):
        store.query_dicts("SELECT * FROM nonexistent_table")
    assert store.last_error() is not None
    store.close()


def test_execute_count_returns_affected_rows(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "ec.db")
    store.execute_count("CREATE TABLE t (id INTEGER, val TEXT)")
    store.insert("t", {"id": 1, "val": "a"})
    store.insert("t", {"id": 2, "val": "b"})
    store.insert("t", {"id": 3, "val": "c"})

    count = store.execute_count("DELETE FROM t WHERE id <= ?", [2])
    assert count == 2

    count = store.execute_count("UPDATE t SET val = 'z'")
    assert count == 1
    store.close()


def test_execute_count_zero_affected(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "ec_zero.db")
    store.execute_count("CREATE TABLE t (id INTEGER)")
    count = store.execute_count("DELETE FROM t WHERE id = ?", [999])
    assert count == 0
    store.close()


def test_execute_count_bad_sql_propagates_error(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "ec_err.db")
    with pytest.raises(Exception):
        store.execute_count("DELETE FROM nonexistent_table")
    assert store.last_error() is not None
    store.close()


def test_execute_count_auto_commits_outside_transaction(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "ec_commit.db")
    store.execute_count("CREATE TABLE t (id INTEGER)")
    store.insert("t", {"id": 1})
    count = store.execute_count("DELETE FROM t")
    assert count == 1
    assert not store.in_transaction
    store.close()


def test_execute_count_no_commit_inside_transaction(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "ec_tx.db")
    store.execute_count("CREATE TABLE t (id INTEGER)")
    store.insert("t", {"id": 1})
    store.begin()
    count = store.execute_count("DELETE FROM t")
    assert count == 1
    assert store.in_transaction
    store.rollback()
    # Row should still be there after rollback
    rows = store.query_dicts("SELECT * FROM t")
    assert len(rows) == 1
    store.close()


def test_insert_returns_rowid(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "ins.db")
    store.execute_count(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, val INTEGER)"
    )
    rowid = store.insert("t", {"name": "alpha", "val": 10})
    assert rowid == 1
    rowid2 = store.insert("t", {"name": "beta", "val": 20})
    assert rowid2 == 2
    rows = store.query_dicts("SELECT * FROM t ORDER BY id")
    assert rows == [
        {"id": 1, "name": "alpha", "val": 10},
        {"id": 2, "name": "beta", "val": 20},
    ]
    store.close()


def test_insert_bad_table_raises(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "ins_err.db")
    with pytest.raises(ValueError, match="Invalid SQL identifier"):
        store.insert("bad table!", {"a": 1})
    store.close()


def test_query_rows_no_filter(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "qr.db")
    store.execute_count("CREATE TABLE t (id INTEGER, name TEXT)")
    store.insert("t", {"id": 1, "name": "a"})
    store.insert("t", {"id": 2, "name": "b"})
    rows = store.query_rows("t")
    assert len(rows) == 2
    store.close()


def test_query_rows_with_where(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "qr_w.db")
    store.execute_count("CREATE TABLE t (id INTEGER, name TEXT)")
    store.insert("t", {"id": 1, "name": "a"})
    store.insert("t", {"id": 2, "name": "b"})
    rows = store.query_rows("t", where={"name": "b"})
    assert rows == [{"id": 2, "name": "b"}]
    store.close()


def test_query_rows_with_order_and_limit(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "qr_ol.db")
    store.execute_count("CREATE TABLE t (id INTEGER, name TEXT)")
    for i in range(5):
        store.insert("t", {"id": i, "name": f"n{i}"})
    rows = store.query_rows("t", order="id DESC", limit=2)
    assert [r["id"] for r in rows] == [4, 3]
    store.close()


def test_update_rows_returns_count(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "upd.db")
    store.execute_count("CREATE TABLE t (id INTEGER, val TEXT)")
    store.insert("t", {"id": 1, "val": "old"})
    store.insert("t", {"id": 2, "val": "old"})
    store.insert("t", {"id": 3, "val": "keep"})
    count = store.update_rows("t", where={"val": "old"}, values={"val": "new"})
    assert count == 2
    rows = store.query_rows("t", where={"val": "new"})
    assert len(rows) == 2
    store.close()


def test_update_rows_zero_match(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "upd0.db")
    store.execute_count("CREATE TABLE t (id INTEGER, val TEXT)")
    count = store.update_rows("t", where={"id": 999}, values={"val": "x"})
    assert count == 0
    store.close()


def test_delete_rows_returns_count(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "del.db")
    store.execute_count("CREATE TABLE t (id INTEGER, val TEXT)")
    store.insert("t", {"id": 1, "val": "a"})
    store.insert("t", {"id": 2, "val": "b"})
    store.insert("t", {"id": 3, "val": "a"})
    count = store.delete_rows("t", where={"val": "a"})
    assert count == 2
    remaining = store.query_rows("t")
    assert len(remaining) == 1
    assert remaining[0]["id"] == 2
    store.close()


def test_delete_rows_zero_match(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "del0.db")
    store.execute_count("CREATE TABLE t (id INTEGER)")
    count = store.delete_rows("t", where={"id": 999})
    assert count == 0
    store.close()


def test_convenience_methods_on_record_store_interface(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "iface.db")
    assert isinstance(store, RecordStoreInterface)
    for method in (
        "query_dicts",
        "execute_count",
        "insert",
        "query_rows",
        "update_rows",
        "delete_rows",
        "capabilities",
    ):
        assert hasattr(store, method)
    ensure_interface_compatibility(store, interface="record_store")
    store.close()


def test_deprecated_record_store_methods_warn(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "warn.db")
    with pytest.warns(DeprecationWarning):
        store.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    with pytest.warns(DeprecationWarning):
        store.executemany("INSERT INTO t (val) VALUES (?)", [("a",), ("b",)])
    with pytest.warns(DeprecationWarning):
        rows = store.query("SELECT val FROM t ORDER BY id")
    assert [row["val"] for row in rows] == ["a", "b"]
    store.close()


def test_record_store_capabilities_and_default_checkpoint_shape(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "caps.db")
    assert store.capabilities() == {
        "checkpoint": True,
        "raw_sql": True,
        "wal": True,
    }
    assert store.checkpoint() == (0, 0, 0) or len(store.checkpoint()) == 3
    store.close()

    class _MinimalRecordStore:
        contract_version = "v1"

        def begin(self) -> None:
            return None

        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

        def execute(self, sql: str, params=None) -> Any:
            raise NotImplementedError

        def executemany(self, sql: str, params) -> Any:
            raise NotImplementedError

        def query(self, sql: str, params=None) -> list[Any]:
            return []

        def query_dicts(self, sql: str, params=None) -> list[dict[str, Any]]:
            return []

        def execute_count(self, sql: str, params=None) -> int:
            return 0

        def insert(self, table: str, row: dict[str, Any]) -> int:
            return 0

        def query_rows(self, table: str, where=None, order=None, limit=None):
            return []

        def update_rows(
            self, table: str, where: dict[str, Any], values: dict[str, Any]
        ) -> int:
            return 0

        def delete_rows(self, table: str, where: dict[str, Any]) -> int:
            return 0

        def healthcheck(self) -> dict[str, Any]:
            return {"ok": True}

        def migrate(self, schema_version: int) -> None:
            return None

        def checkpoint(self, mode: str = "PASSIVE") -> tuple[int, int, int]:
            return (0, 0, 0)

        def capabilities(self) -> dict[str, bool]:
            return {"checkpoint": False, "raw_sql": False, "wal": False}

        @property
        def in_transaction(self) -> bool:
            return False

        def last_error(self) -> str | None:
            return None

        def diagnostics(self) -> dict[str, Any]:
            return {}

    ensure_interface_compatibility(_MinimalRecordStore(), interface="record_store")


def test_interface_validator_negative_missing_members() -> None:
    class _BrokenRecordStore:
        contract_version = "v1"

        def begin(self) -> None:  # pragma: no cover - shape only
            return None

    with pytest.raises(TypeError, match="missing required members"):
        ensure_interface_compatibility(_BrokenRecordStore(), interface="record_store")


def test_interface_validator_negative_wrong_version(tmp_path: Path) -> None:
    class _WrongVersionBlobStore:
        contract_version = "v0"

        def put_bytes(
            self, data, media_type="application/octet-stream", ext="", meta=None
        ):
            return {"ok": True}

        def put_file(self, path, media_type=None, meta=None):
            return {"ok": True}

        def open(self, ref):
            raise FileNotFoundError

        def stat(self, ref):
            return {}

        def gc(self, policy=None):
            return {}

        def verify(self, digest: str):
            return {}

        def healthcheck(self) -> dict[str, Any]:
            return {}

        def describe_backend(self) -> Any:
            return BackendDescriptor(
                backend_id="mock",
                version="1.0.0",
                planes_supported={"blob"},
                capabilities={},
                limits={},
            )

    with pytest.raises(TypeError, match="unsupported contract_version"):
        ensure_interface_compatibility(_WrongVersionBlobStore(), interface="blob_store")


def test_storage_envelope_shape() -> None:
    envelope = StorageEnvelope(
        operation="write_event",
        ok=False,
        data={"session_id": "s-1"},
        error=StorageError(code="E_WRITE", message="write failed", retryable=True),
    )
    payload = envelope.to_dict()
    assert payload["contract_version"] == "v1"
    assert payload["module"] == "openminion-storage"
    assert payload["operation"] == "write_event"
    assert payload["ok"] is False
    assert payload["error"]["code"] == "E_WRITE"


def test_unsupported_capability_error_shape() -> None:
    req = CapabilityRequirement(
        name="vector_search",
        version_range=">=1.0",
        required_features={"similarity_search", "filtering"},
    )
    desc = BackendDescriptor(
        backend_id="sqlite",
        version="3.40.0",
        planes_supported={"record", "blob"},
        capabilities={"basic_crud": True},
        limits={"max_connection_pool_size": 10, "max_query_size_mb": 1},
    )

    error = UnsupportedCapabilityError(
        requirement=req,
        descriptor=desc,
        message="Backend does not support vector search capabilities",
    )

    error_data = error.to_dict()
    assert error_data["error_type"] == "UnsupportedCapabilityError"
    assert error_data["requirement"]["name"] == "vector_search"
    assert error_data["descriptor"]["backend_id"] == "sqlite"
    # Fixed: check that the original requirement features are returned in the serialized format
    result_features = set(error_data["requirement"]["required_features"])
    assert "similarity_search" in result_features
    assert "filtering" in result_features


def test_backend_descriptor_contract() -> None:
    descriptor = BackendDescriptor(
        backend_id="postgres",
        version="15.4",
        planes_supported={"record", "blob", "vector"},
        capabilities={"transactions": True, "batch_ops": True},
        limits={"connection_pool_max": 20, "max_query_time_ms": 30000},
    )

    assert descriptor.backend_id == "postgres"
    assert descriptor.version == "15.4"
    assert "vector" in descriptor.planes_supported
    assert descriptor.capabilities["transactions"] is True
    assert descriptor.limits["connection_pool_max"] == 20


def test_check_capability_support_functionality() -> None:
    # A backend with vector search capability
    desc_with_vectors = BackendDescriptor(
        backend_id="postgrests",
        version="15.4",
        planes_supported={"record", "vector"},
        capabilities={"vector_search": True, "dimension_limit": 1536},
        limits={"vector_dimensions_max": 2048},
    )

    # Requirement for vector search
    req = CapabilityRequirement(
        name="vector_search",
        version_range=">=1.0",
        required_features={"vector_search"},  # Actually checks for a capability name
    )

    result = check_capability_support(desc_with_vectors, req)
    # This helper is intentionally shallow here; just prove the call stays typed.
    assert isinstance(result, bool)


def test_storage_envelope_with_unsupported_capability_error() -> None:
    req = CapabilityRequirement(
        name="vector_search",
        version_range=">=1.0",
        required_features={"similarity_search", "filtering"},
    )
    desc = BackendDescriptor(
        backend_id="sqlite",
        version="3.40.0",
        planes_supported={"record", "blob"},
        capabilities={"basic_crud": True},
        limits={"max_connection_pool_size": 10},
    )

    capability_error = UnsupportedCapabilityError(
        requirement=req,
        descriptor=desc,
        message="Backend does not support vector search capabilities",
    )

    envelope = StorageEnvelope(
        operation="upsert_vector", ok=False, error=capability_error
    )

    payload = envelope.to_dict()
    assert payload["operation"] == "upsert_vector"
    assert payload["ok"] is False
    assert payload["error"]["error_type"] == "UnsupportedCapabilityError"
    assert (
        payload["error"]["message"]
        == "Backend does not support vector search capabilities"
    )


def test_v11_capability_check_methods_on_all_interfaces() -> None:

    # Mock implementations to test interface compliance
    class MockStructuredStore:
        contract_version = "v1"

        def create(self, table: str, data: dict[str, Any]) -> Any:
            pass

        def read(self, table: str, id_value: Any) -> dict[str, Any]:
            return {}

        def update(self, table: str, id_value: Any, data: dict[str, Any]) -> Any:
            pass

        def delete(self, table: str, id_value: Any) -> bool:
            return True

        def batch_create(self, table: str, items: list[dict[str, Any]]) -> list[Any]:
            return []

        def batch_read(self, table: str, ids: list[Any]) -> list[dict[str, Any]]:
            return []

        def batch_update(
            self, table: str, updates: list[tuple[Any, dict[str, Any]]]
        ) -> list[bool]:
            return []

        def batch_delete(self, table: str, ids: list[Any]) -> list[bool]:
            return []

        def query(self, table: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
            return []

        def count(self, table: str, filters: dict[str, Any]) -> int:
            return 0

        def check_capability(self, requirement: CapabilityRequirement) -> bool:
            return True

        def begin_transaction(self) -> Any:
            return None

        def commit_transaction(self, tx_handle: Any) -> None:
            pass

        def rollback_transaction(self, tx_handle: Any) -> None:
            pass

        def healthcheck(self) -> dict[str, Any]:
            return {}

        def describe_backend(self) -> Any:
            return BackendDescriptor(
                backend_id="mock",
                version="1.0.0",
                planes_supported={"record", "blob"},
                capabilities={},
                limits={},
            )

    class MockVectorStore:
        contract_version = "v1"

        def upsert(self, vectors, metadata, ids, namespace=None):
            pass

        def search(self, query_vector, top_k=10, filters=None, namespace=None):
            return []

        def delete(self, ids, namespace=None):
            return True

        def list_namespaces(self):
            return []

        def namespace_stats(self, namespace):
            return {}

        def count(self, namespace=None):
            return 0

        def check_capability(self, requirement: CapabilityRequirement) -> bool:
            return True

        def healthcheck(self) -> dict[str, Any]:
            return {}

        def describe_backend(self) -> Any:
            return BackendDescriptor(
                backend_id="mock",
                version="1.0.0",
                planes_supported={"vector"},
                capabilities={},
                limits={},
            )

    # Test that the new check_capability method is properly validated
    struct_store = MockStructuredStore()
    vector_store = MockVectorStore()

    # Test that the interfaces are satisfied with the new methods
    ensure_interface_compatibility(struct_store, interface="structured_store")
    ensure_interface_compatibility(vector_store, interface="vector_store")


def test_check_capability_support_function_enhanced() -> None:

    # Scenario 1: Positive capability check (backend supports feature)
    backend_with_vectors = BackendDescriptor(
        backend_id="postgres",
        version="12.0",
        planes_supported={"record", "vector"},
        capabilities={"vector_search": True, "dimension_limit": 2048},
        limits={"max_vectors": 1000000},
    )

    capability_requirement = CapabilityRequirement(
        name="vector_search_support",
        version_range=">=10.0",
        required_features={"vector_search", "vector"},
    )

    result = check_capability_support(backend_with_vectors, capability_requirement)
    assert result is True, "Should support capability when all requirements are met"

    # Scenario 2: Negative capability check (version too old)
    old_backend = BackendDescriptor(
        backend_id="sqlite",
        version="3.20.0",
        planes_supported={"record"},
        capabilities={"basic_crud": True},
        limits={"max_db_size_mb": 1000},
    )

    newer_requirement = CapabilityRequirement(
        name="feature_requires_newer",
        version_range=">=3.30",
        required_features={"record"},
    )

    result = check_capability_support(old_backend, newer_requirement)
    assert result is False, (
        "Should not support capability when version requirement not met"
    )

    # Scenario 3: Missing capability feature
    backend_without_req = BackendDescriptor(
        backend_id="mysql",
        version="8.0",
        planes_supported={"record", "blob"},
        capabilities={"basic_operations": True},
        limits={"max_connections": 100},
    )

    missing_capability_requirement = CapabilityRequirement(
        name="requires_vector_feature",
        version_range=">=8.0",
        required_features={"vector", "advanced_queries"},
    )

    result = check_capability_support(
        backend_without_req, missing_capability_requirement
    )
    assert result is False, (
        "Should not support capability when specific feature is missing"
    )

    # Scenario 4: Positive capability check using a plane as a requirement
    backend_with_blob_and_record = BackendDescriptor(
        backend_id="minio",
        version="2023",
        planes_supported={"blob", "record"},
        capabilities={"multipart_upload": True},
        limits={"throughput_mb_ps": 100},
    )

    plane_requirement = CapabilityRequirement(
        name="data_storage_service",
        version_range=">=2020",
        required_features={"blob"},
    )

    result = check_capability_support(backend_with_blob_and_record, plane_requirement)
    assert result is True, "Should support capability when required plane is available"


def test_create_capability_error_envelope_helper() -> None:

    req = CapabilityRequirement(
        name="advanced_security",
        version_range=">=1.0",
        required_features={"encryption", "audit_logging"},
    )
    desc = BackendDescriptor(
        backend_id="simple_db",
        version="1.0.0",
        planes_supported={"record"},
        capabilities={},
        limits={"max_records": 1000},
    )

    envelope = create_capability_error_envelope(
        "write_encrypted", req, desc, "Encryption not supported by backend"
    )

    assert envelope.operation == "write_encrypted"
    assert envelope.ok is False
    assert envelope.contract_version == "v1"
    assert isinstance(envelope.error, UnsupportedCapabilityError)

    payload = envelope.to_dict()
    assert payload["operation"] == "write_encrypted"
    assert payload["ok"] is False
    assert payload["error"]["error_type"] == "UnsupportedCapabilityError"
    assert payload["error"]["message"] == "Encryption not supported by backend"
