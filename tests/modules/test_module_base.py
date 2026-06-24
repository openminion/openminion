from openminion.modules.base import ModuleBase, ModuleDescriptor


def test_module_descriptor_creation():
    descriptor = ModuleDescriptor(name="test", version="1.0.0")
    assert descriptor.name == "test"
    assert descriptor.version == "1.0.0"
    assert descriptor.contract_version == "v1"


def test_module_base_initialization():
    descriptor = ModuleDescriptor(name="test", version="1.0.0")
    base = ModuleBase(descriptor=descriptor)
    assert base.descriptor.name == "test"
    assert base.config == {}


def test_module_base_preserves_explicit_empty_config():
    descriptor = ModuleDescriptor(name="test", version="1.0.0")
    config: dict[str, object] = {}
    base = ModuleBase(descriptor=descriptor, config=config)
    assert base.config is config


def test_module_base_healthcheck():
    descriptor = ModuleDescriptor(name="test", version="1.0.0")
    base = ModuleBase(descriptor=descriptor)
    health = base.healthcheck()
    assert health["status"] == "ok"
    assert health["module"] == "test"


def test_module_base_close():
    descriptor = ModuleDescriptor(name="test")
    base = ModuleBase(descriptor=descriptor)
    base.close()  # Should not raise
