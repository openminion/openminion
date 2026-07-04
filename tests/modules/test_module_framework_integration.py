from __future__ import annotations

import importlib
import sys
import pytest
from typing import Any
from pathlib import Path

from openminion.base.version import OPENMINION_VERSION
from openminion.modules.base import ModuleBase, ModuleDescriptor
from openminion.modules.providers import (
    ModuleRegistry,
    normalize_contract_version,
    check_contract_version_compatibility,
    ProviderNotFoundError,
    ContractVersionError,
)

_EXAMPLES_MODULES_ROOT = Path(__file__).resolve().parents[2] / "examples" / "modules"
sys.path.insert(0, str(_EXAMPLES_MODULES_ROOT))

_sample_service = importlib.import_module("sample.service")
_sample_config = importlib.import_module("sample.config")
_sample_provider = importlib.import_module("sample.provider")

SampleServiceImpl = _sample_service.SampleServiceImpl
SampleConfig = _sample_config.SampleConfig
create_sample_provider_registry = _sample_provider.create_sample_provider_registry
get_sample_provider = _sample_provider.get_sample_provider
validate_provider_config = _sample_provider.validate_provider_config


class TestModuleBaseIntegration:
    def test_module_descriptor_full_initialization(self):
        descriptor = ModuleDescriptor(
            name="test_module",
            version="2.0.0",
            contract_version="v1",
            provider_id="test_provider",
            config={"key": "value"},
        )

        assert descriptor.name == "test_module"
        assert descriptor.version == "2.0.0"
        assert descriptor.contract_version == "v1"
        assert descriptor.provider_id == "test_provider"
        assert descriptor.config == {"key": "value"}

    def test_module_descriptor_defaults(self):
        descriptor = ModuleDescriptor(name="minimal")

        assert descriptor.name == "minimal"
        assert descriptor.version == OPENMINION_VERSION
        assert descriptor.contract_version == "v1"
        assert descriptor.provider_id is None
        assert descriptor.config == {}

    def test_module_base_with_custom_descriptor(self):
        descriptor = ModuleDescriptor(
            name="custom",
            version="1.5.0",
            contract_version="v1",
        )
        config = {"setting": "value"}

        base = ModuleBase(descriptor=descriptor, config=config)

        assert base.descriptor.name == "custom"
        assert base.descriptor.version == "1.5.0"
        assert base.config["setting"] == "value"

    def test_module_base_immutable_descriptor(self):
        descriptor = ModuleDescriptor(name="test")
        base = ModuleBase(descriptor=descriptor)

        with pytest.raises(AttributeError):
            base.descriptor = ModuleDescriptor(name="new")

    def test_module_base_healthcheck_extension(self):

        class ExtendedModule(ModuleBase):
            def healthcheck(self) -> dict[str, Any]:
                health = super().healthcheck()
                health["extended"] = True
                return health

        descriptor = ModuleDescriptor(
            name="extended", version=OPENMINION_VERSION
        )
        module = ExtendedModule(descriptor=descriptor)
        health = module.healthcheck()

        assert health["status"] == "ok"
        assert health["extended"] is True


class TestModuleRegistryIntegration:
    def test_registry_multiple_providers(self):
        registry = ModuleRegistry[str]()

        registry.register("provider_a", "value_a", contract_version="v1")
        registry.register("provider_b", "value_b", contract_version="v1")
        registry.register("provider_c", "value_c", contract_version="v1")

        assert registry.get("provider_a") == "value_a"
        assert registry.get("provider_b") == "value_b"
        assert registry.get("provider_c") == "value_c"
        assert len(registry.list_providers()) == 3

    def test_registry_version_normalization_variants(self):
        registry = ModuleRegistry[str](expected_contract_version="v1")

        registry.register("v1_plain", "val1", contract_version="v1")
        registry.register("v1_dot", "val2", contract_version="v1.0")
        registry.register("v1_nov", "val3", contract_version="1.0")
        registry.register("v1_major", "val4", contract_version="1")

        assert registry.get("v1_plain") == "val1"
        assert registry.get("v1_dot") == "val2"
        assert registry.get("v1_nov") == "val3"
        assert registry.get("v1_major") == "val4"

    def test_registry_rejects_incompatible_version(self):
        registry = ModuleRegistry[str](expected_contract_version="v1")

        with pytest.raises(ContractVersionError):
            registry.register("v2_provider", "value", contract_version="v2")

    def test_registry_generic_type_safety(self):

        class FakeProvider:
            def __init__(self, name: str):
                self.name = name

        registry = ModuleRegistry[FakeProvider]()
        registry.register("fake", FakeProvider("test"))

        provider = registry.get("fake")
        assert isinstance(provider, FakeProvider)
        assert provider.name == "test"


class TestContractVersionNormalization:
    def test_normalize_v1_variants(self):
        assert normalize_contract_version("v1") == "v1"
        assert normalize_contract_version("V1") == "v1"
        assert normalize_contract_version("v1.0") == "v1"
        assert normalize_contract_version("v1.0.0") == "v1"
        assert normalize_contract_version("1") == "v1"
        assert normalize_contract_version("1.0") == "v1"
        assert normalize_contract_version("  v1  ") == "v1"

    def test_normalize_v2_variants(self):
        assert normalize_contract_version("v2") == "v2"
        assert normalize_contract_version("v2.0") == "v2"
        assert normalize_contract_version("2") == "v2"
        assert normalize_contract_version("2.5") == "v2"

    def test_normalize_invalid_version(self):
        with pytest.raises(ContractVersionError):
            normalize_contract_version("invalid")

        with pytest.raises(ContractVersionError):
            normalize_contract_version("")

    def test_compatibility_same_version(self):
        assert check_contract_version_compatibility("v1", "v1") is True
        assert check_contract_version_compatibility("v1.0", "v1") is True
        assert check_contract_version_compatibility("v1", "v1.0") is True

    def test_compatibility_different_major(self):
        with pytest.raises(ContractVersionError):
            check_contract_version_compatibility("v2", "v1")

        with pytest.raises(ContractVersionError):
            check_contract_version_compatibility("v1", "v2")

    def test_compatibility_allow_higher(self):
        assert (
            check_contract_version_compatibility("v2", "v1", allow_higher=True) is True
        )
        assert (
            check_contract_version_compatibility("v3", "v1", allow_higher=True) is True
        )

        with pytest.raises(ContractVersionError):
            check_contract_version_compatibility("v1", "v2", allow_higher=True)


class TestSampleModule:
    def test_sample_service_protocol_compliance(self):
        service = SampleServiceImpl()

        assert hasattr(service, "contract_version")
        assert service.contract_version == "v1"
        assert hasattr(service, "healthcheck")
        assert hasattr(service, "process")
        assert hasattr(service, "close")

    def test_sample_service_default_initialization(self):
        service = SampleServiceImpl()

        assert service.descriptor.name == "sample"
        assert service.descriptor.version == "1.0.0"
        assert service.descriptor.contract_version == "v1"

    def test_sample_service_custom_config(self):
        config = {"prefix": "[", "suffix": "]"}
        service = SampleServiceImpl(config=config)

        result = service.process("test")
        assert result["output"] == "[test]"
        assert result["success"] is True

    def test_sample_service_healthcheck(self):
        service = SampleServiceImpl()
        health = service.healthcheck()

        assert health["status"] == "ok"
        assert health["module"] == "sample"
        assert health["version"] == "1.0.0"
        assert health["initialized"] is True

    def test_sample_service_close(self):
        service = SampleServiceImpl()

        assert service.healthcheck()["initialized"] is True

        service.close()

        assert service.healthcheck()["initialized"] is False
        result = service.process("test")
        assert result["success"] is False
        assert result["error"] == "Service not initialized"

    def test_sample_config_validation(self):
        config = SampleConfig(
            provider_id="test",
            prefix="pre",
            suffix="suf",
            enabled=True,
            metadata={"key": "value"},
        )

        assert config.provider_id == "test"
        assert config.prefix == "pre"
        assert config.suffix == "suf"
        assert config.enabled is True
        assert config.metadata == {"key": "value"}

    def test_sample_config_empty_provider_id_raises(self):
        with pytest.raises(ValueError, match="provider_id cannot be empty"):
            SampleConfig(provider_id="")

    def test_sample_config_from_dict(self):
        data = {
            "provider_id": "from_dict",
            "prefix": "p",
            "suffix": "s",
            "enabled": False,
        }

        config = SampleConfig.from_dict(data)

        assert config.provider_id == "from_dict"
        assert config.prefix == "p"
        assert config.suffix == "s"
        assert config.enabled is False

    def test_sample_config_to_dict(self):
        config = SampleConfig(provider_id="test", prefix="p")
        data = config.to_dict()

        assert data["provider_id"] == "test"
        assert data["prefix"] == "p"
        assert data["suffix"] == ""
        assert data["enabled"] is True


class TestSampleProviderRegistry:
    def test_create_registry_with_defaults(self):
        registry = create_sample_provider_registry()

        providers = registry.list_providers()
        assert "default" in providers
        assert "uppercase" in providers
        assert len(providers) == 2

    def test_get_default_provider(self):
        registry = create_sample_provider_registry()
        provider = get_sample_provider(registry, "default")

        assert provider is not None
        assert provider.contract_version == "v1"

        result = provider.process("hello")
        assert result["output"] == "hello"

    def test_get_uppercase_provider(self):
        registry = create_sample_provider_registry()
        provider = get_sample_provider(registry, "uppercase")

        result = provider.process("test")
        assert result["output"] == "[test]"

    def test_get_unknown_provider_fails_fast(self):
        registry = create_sample_provider_registry()

        with pytest.raises(ProviderNotFoundError) as exc_info:
            get_sample_provider(registry, "unknown_provider")

        assert "unknown_provider" in str(exc_info.value)
        assert "Available providers" in str(exc_info.value)

    def test_validate_provider_config_valid(self):
        config = SampleConfig(provider_id="valid", enabled=True)
        is_valid, error = validate_provider_config(config)

        assert is_valid is True
        assert error == ""

    def test_validate_provider_config_empty_id_raises(self):
        with pytest.raises(ValueError, match="provider_id cannot be empty"):
            SampleConfig(provider_id="", enabled=True)

    def test_validate_provider_config_disabled(self):
        config = SampleConfig(provider_id="test", enabled=False)
        is_valid, error = validate_provider_config(config)

        assert is_valid is False
        assert "provider is disabled" in error


class TestModuleFrameworkEndToEnd:
    def test_full_provider_lifecycle(self):
        registry = create_sample_provider_registry()

        provider = get_sample_provider(registry, "default", {})

        health_before = provider.healthcheck()
        assert health_before["initialized"] is True

        result = provider.process("lifecycle_test")
        assert result["success"] is True
        assert result["input"] == "lifecycle_test"

        provider.close()

        health_after = provider.healthcheck()
        assert health_after["initialized"] is False

    def test_multiple_provider_isolation(self):
        registry = create_sample_provider_registry()

        default = get_sample_provider(registry, "default")
        uppercase = get_sample_provider(registry, "uppercase")

        default_result = default.process("shared")
        uppercase_result = uppercase.process("shared")

        assert default_result["output"] == "shared"
        assert uppercase_result["output"] == "[shared]"

    def test_registry_fail_fast_preserved(self):
        registry = ModuleRegistry[str]()
        registry.register("known", "value")

        with pytest.raises(ProviderNotFoundError) as exc_info:
            registry.get("unknown")

        assert "unknown" in str(exc_info.value)
        assert "Available providers" in str(exc_info.value)
        assert "known" in str(exc_info.value)
