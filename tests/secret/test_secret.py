import pytest
import asyncio
from pathlib import Path

from openminion.modules.secret.service import (
    SecretService,
    SecretKeyError,
    SecretNotFoundError,
)
from openminion.modules.secret.loader import SecretConfigLoader, SECRET_PATTERN


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def master_key():
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


@pytest.fixture
def service(tmp_path: Path, master_key: str):
    instance = SecretService(str(tmp_path / "secret.db"), master_key)
    yield instance
    _run(instance.close())


def test_set_and_get_secret(service):
    _run(service.set_secret("test_key", "test_value"))
    value = _run(service.get_secret("test_key"))
    assert value == "test_value"


def test_get_secret_not_found(service):
    with pytest.raises(SecretNotFoundError):
        _run(service.get_secret("nonexistent"))


def test_delete_secret(service):
    _run(service.set_secret("test_key", "test_value"))
    _run(service.delete_secret("test_key"))
    with pytest.raises(SecretNotFoundError):
        _run(service.get_secret("test_key"))


def test_list_keys(service):
    _run(service.set_secret("key1", "value1"))
    _run(service.set_secret("key2", "value2"))
    keys = _run(service.list_keys())
    assert "key1" in keys
    assert "key2" in keys


def test_namespace_isolation(service):
    _run(service.set_secret("key1", "value1", namespace="ns1"))
    _run(service.set_secret("key1", "value2", namespace="ns2"))

    v1 = _run(service.get_secret("key1", namespace="ns1"))
    v2 = _run(service.get_secret("key1", namespace="ns2"))

    assert v1 == "value1"
    assert v2 == "value2"


def test_missing_master_key(monkeypatch):
    monkeypatch.delenv("OPENMINION_SECRET_KEY", raising=False)
    with pytest.raises(SecretKeyError):
        SecretService()


def test_secret_pattern():
    assert SECRET_PATTERN.match("$SECRET:my_key") is not None
    assert SECRET_PATTERN.match("$SECRET:api-key_123") is not None
    assert SECRET_PATTERN.match("$SECRET:") is None
    assert SECRET_PATTERN.match("not_a_secret") is None
    assert SECRET_PATTERN.match("${SECRET:key}") is None


def test_config_loader_resolve(service):
    _run(service.set_secret("openai_api_key", "sk-secret123"))
    loader = SecretConfigLoader(service)
    config = {"api_key": "$SECRET:openai_api_key", "other": "value"}
    resolved = _run(loader.resolve_config(config))

    assert resolved["api_key"] == "sk-secret123"
    assert resolved["other"] == "value"


def test_config_loader_nested(service):
    _run(service.set_secret("nested_key", "nested_value"))
    loader = SecretConfigLoader(service)
    config = {"level1": {"level2": {"api_key": "$SECRET:nested_key"}}}
    resolved = _run(loader.resolve_config(config))

    assert resolved["level1"]["level2"]["api_key"] == "nested_value"
