import pytest
from unittest.mock import Mock
from openminion.modules.memory.interfaces import (
    MEMORY_INTERFACE_VERSION,
    ensure_memory_compatibility,
)
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import (
    MemoryStore,
)


class TestMemoryServiceContractVersion:
    def test_memory_service_contract_version_declared(self):
        mock_store = Mock(spec=MemoryStore)

        service = MemoryService(store=mock_store)
        assert hasattr(service, "contract_version")
        assert service.contract_version == MEMORY_INTERFACE_VERSION


class TestMemoryServiceCompatibilityValidator:
    def test_memory_service_valid_implementation_passes(self):
        mock_store = Mock(spec=MemoryStore)

        mock_store.put = Mock(return_value="record_id")
        mock_store.upsert = Mock()
        mock_store.get = Mock(return_value=None)
        mock_store.delete = Mock()
        mock_store.tombstone = Mock()
        mock_store.list = Mock(return_value=[])
        mock_store.search = Mock(return_value=[])
        mock_store.retrieve_by_entities = Mock(return_value=[])
        mock_store.candidate_put = Mock(return_value="candidate_id")
        mock_store.candidate_list = Mock(return_value=[])
        mock_store.candidate_update = Mock()
        mock_store.promote_candidate = Mock()
        mock_store.history = Mock(return_value=[])

        service = MemoryService(store=mock_store)
        success, errors = ensure_memory_compatibility(service, strict=False)
        assert success is True
        assert len(errors) == 0

    def test_memory_service_missing_method_fails(self):
        class BrokenService:
            contract_version = MEMORY_INTERFACE_VERSION

        service = BrokenService()
        success, errors = ensure_memory_compatibility(service, strict=False)
        assert success is False
        assert len(errors) > 0
        assert any("Missing required method" in error for error in errors)

    def test_memory_service_version_mismatch_fails(self):
        class WrongVersionService:
            contract_version = "v99"

        service = WrongVersionService()
        success, errors = ensure_memory_compatibility(service, strict=False)
        assert success is False
        assert len(errors) > 0
        assert "Version mismatch" in str(errors[0])

    def test_memory_service_strict_mode_raises_error(self):
        class BadService:
            contract_version = "v99"

        service = BadService()
        with pytest.raises(Exception):
            ensure_memory_compatibility(service, strict=True)
