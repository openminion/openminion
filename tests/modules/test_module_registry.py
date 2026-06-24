import pytest
from openminion.modules.providers import (
    ModuleRegistry,
    normalize_contract_version,
    ProviderNotFoundError,
    DuplicateProviderError,
)


def test_normalize_contract_version():
    assert normalize_contract_version("v1") == "v1"
    assert normalize_contract_version("v1.0") == "v1"
    assert normalize_contract_version("1.0") == "v1"
    assert normalize_contract_version("1") == "v1"


def test_registry_basic():
    registry = ModuleRegistry()
    registry.register("provider1", "value1")
    assert registry.get("provider1") == "value1"


def test_registry_duplicate():
    registry = ModuleRegistry()
    registry.register("provider1", "value1")
    with pytest.raises(DuplicateProviderError):
        registry.register("provider1", "value2")


def test_registry_unknown():
    registry = ModuleRegistry()
    with pytest.raises(ProviderNotFoundError):
        registry.get("unknown")
