import pytest
from unittest.mock import Mock
from openminion.modules.retrieve.interfaces import (
    RETRIEVE_INTERFACE_VERSION,
    RETRIEVE_STORAGE_INTERFACE_VERSION,
    ensure_retrieve_compatibility,
    ensure_retrieve_storage_compatibility,
)
from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
from openminion.modules.retrieve.storage.store import SQLiteRetrieveStore
from openminion.modules.storage.errors import StorageDomainError


class TestRetrieveCtlContractVersion:
    def test_retrieve_ctl_contract_version_declared(self):
        mock_config = Mock()
        mock_config.storage = Mock()
        mock_config.storage.blob_root = "/tmp/retrieve-test-blob"
        mock_config.storage.sqlite_path = ":memory:"
        mock_config.storage.wal_mode = True
        mock_config.defaults = Mock()
        mock_config.defaults.lexical_candidate_count = 10
        mock_config.defaults.snippet_tokens = 20
        mock_config.defaults.raptor_internal_k = 5
        mock_config.defaults.doc_group_min_tokens = 50
        mock_config.defaults.doc_group_max_tokens = 500

        retrieve_ctl = RetrieveCtl(config=mock_config)
        assert hasattr(retrieve_ctl, "contract_version")
        assert retrieve_ctl.contract_version == RETRIEVE_INTERFACE_VERSION


class TestRetrieveCtlCompatibilityValidator:
    def test_retrieve_ctl_valid_implementation_passes(self):
        mock_config = Mock()
        mock_config.storage = Mock()
        mock_config.storage.blob_root = "/tmp/retrieve-test-blob"
        mock_config.storage.sqlite_path = ":memory:"
        mock_config.storage.wal_mode = True
        mock_config.defaults = Mock()
        mock_config.defaults.lexical_candidate_count = 10
        mock_config.defaults.snippet_tokens = 20
        mock_config.defaults.raptor_internal_k = 5
        mock_config.defaults.doc_group_min_tokens = 50
        mock_config.defaults.doc_group_max_tokens = 500

        retrieve_ctl = RetrieveCtl(config=mock_config)
        success, errors = ensure_retrieve_compatibility(retrieve_ctl, strict=False)
        assert success is True
        assert len(errors) == 0

    def test_retrieve_ctl_missing_method_fails(self):

        class BrokenCtl:
            contract_version = RETRIEVE_INTERFACE_VERSION

        ctl = BrokenCtl()
        success, errors = ensure_retrieve_compatibility(ctl, strict=False)
        assert success is False
        assert len(errors) > 0
        assert any("Missing required method" in error for error in errors)

    def test_retrieve_ctl_version_mismatch_fails(self):

        class WrongVersionCtl:
            contract_version = "v99"

        ctl = WrongVersionCtl()
        success, errors = ensure_retrieve_compatibility(ctl, strict=False)
        assert success is False
        assert len(errors) > 0
        assert "Version mismatch" in str(errors[0])

    def test_retrieve_ctl_strict_mode_raises_error(self):

        class BadCtl:
            contract_version = "v99"

        ctl = BadCtl()
        with pytest.raises(Exception):
            ensure_retrieve_compatibility(ctl, strict=True)


class TestRetrieveStorageCompatibilityValidator:
    def test_retrieve_storage_valid_implementation_passes(self):
        store = SQLiteRetrieveStore(":memory:")
        success, errors = ensure_retrieve_storage_compatibility(store, strict=False)
        assert success is True
        assert errors == []
        assert store.contract_version == RETRIEVE_STORAGE_INTERFACE_VERSION
        store.close()

    def test_retrieve_storage_missing_method_fails(self):
        class BrokenStore:
            contract_version = RETRIEVE_STORAGE_INTERFACE_VERSION

            def execute(self, sql: str, params=()):
                return None

            def commit(self):
                return None

        broken = BrokenStore()
        success, errors = ensure_retrieve_storage_compatibility(broken, strict=False)
        assert success is False
        assert any("Missing required member: fetchone" in item for item in errors)
        assert any("Missing required member: fetchall" in item for item in errors)

    def test_retrieve_storage_strict_mode_raises(self):
        class BadVersionStore:
            contract_version = "v0"

            def execute(self, sql: str, params=()):
                return None

            def fetchone(self, sql: str, params=()):
                return None

            def fetchall(self, sql: str, params=()):
                return []

            def commit(self):
                return None

        with pytest.raises(TypeError):
            ensure_retrieve_storage_compatibility(BadVersionStore(), strict=True)

    def test_retrieve_storage_maps_sqlite_error_to_domain_error(self, caplog):
        store = SQLiteRetrieveStore(":memory:")
        with caplog.at_level("WARNING", logger="openminion.storage"):
            with pytest.raises(StorageDomainError) as ctx:
                store.execute("SELECT * FROM table_that_does_not_exist")
        assert ctx.value.code == "RETRIEVE_STORAGE_SQLITE_ERROR"
        assert "sqlite execution failed" in ctx.value.message.lower()
        assert any(
            "storage_domain_error_mapped module=retrieve operation=execute"
            in record.message
            for record in caplog.records
        )
        store.close()
