from unittest.mock import Mock
import pytest

from openminion.modules.registry.interfaces import (
    REGISTRY_INTERFACE_VERSION,
    ensure_registry_compatibility,
)
from openminion.modules.registry.agents import AgentRegistry


class TestRegistryContractVersion:
    def test_registry_contract_version_declared(self):
        # Create a mock store for AgentRegistry
        mock_store = Mock()
        mock_store.close = Mock()
        mock_store.list_agent_records = Mock(return_value=[])
        mock_store.get_agent = Mock(return_value=None)
        mock_store.upsert_agent = Mock()
        mock_store.delete_agent = Mock()
        mock_store.find_agent_ids_by_method = Mock(return_value=[])
        mock_store.get_agent_record = Mock(return_value=None)
        mock_store.get_status = Mock()
        mock_store.upsert_status = Mock()

        registry = AgentRegistry(store=mock_store)
        assert hasattr(registry, "contract_version")
        assert registry.contract_version == REGISTRY_INTERFACE_VERSION


class TestRegistryCompatibilityValidator:
    def test_registry_valid_implementation_passes(self):
        # Create a mock store for AgentRegistry
        mock_store = Mock()
        mock_store.close = Mock()
        mock_store.list_agent_records = Mock(return_value=[])
        mock_store.get_agent = Mock(return_value=None)
        mock_store.upsert_agent = Mock()
        mock_store.delete_agent = Mock()
        mock_store.find_agent_ids_by_method = Mock(return_value=[])
        mock_store.get_agent_record = Mock(return_value=None)
        mock_store.get_status = Mock(return_value=None)
        mock_store.upsert_status = Mock()

        registry = AgentRegistry(store=mock_store)
        success, errors = ensure_registry_compatibility(registry, strict=False)
        assert success is True
        assert len(errors) == 0

    def test_registry_missing_method_fails(self):

        class BrokenRegistry:
            contract_version = REGISTRY_INTERFACE_VERSION
            # Missing required methods like load, get, register, etc.

        registry = BrokenRegistry()
        success, errors = ensure_registry_compatibility(registry, strict=False)
        assert success is False
        assert len(errors) > 0
        assert any("Missing required method" in error for error in errors)

    def test_registry_version_mismatch_fails(self):

        class WrongVersionRegistry:
            contract_version = "v99"  # Wrong version

        registry = WrongVersionRegistry()
        success, errors = ensure_registry_compatibility(registry, strict=False)
        assert success is False
        assert len(errors) > 0
        assert "Version mismatch" in str(errors[0])

    def test_registry_strict_mode_raises_error(self):

        class BadRegistry:
            contract_version = "v99"  # Wrong version

        registry = BadRegistry()
        with pytest.raises(Exception):  # RegistryError will be raised
            ensure_registry_compatibility(registry, strict=True)
