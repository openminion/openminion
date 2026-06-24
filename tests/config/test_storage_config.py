from __future__ import annotations

import pytest

from openminion.base.config.core import StorageConfig, ConfigValidationError
from openminion.base.config.parser import openminion_config_from_dict


class TestStorageConfigDefaults:
    def test_defaults(self):
        cfg = StorageConfig()
        assert cfg.backend == "sqlite"
        assert cfg.postgres_url == ""
        assert cfg.postgres_pool_min == 1
        assert cfg.postgres_pool_max == 5

    def test_record_backend_sqlite(self):
        cfg = StorageConfig(backend="sqlite")
        assert cfg.record_backend() == "record.sqlite"
        assert cfg.record_backend_options() == {}

    def test_record_backend_postgres(self):
        cfg = StorageConfig(
            backend="postgres", postgres_url="postgresql://localhost/test"
        )
        assert cfg.record_backend() == "record.postgres"
        opts = cfg.record_backend_options()
        assert opts["url"] == "postgresql://localhost/test"
        assert opts["pool_min"] == 1
        assert opts["pool_max"] == 5


class TestStorageConfigValidation:
    def test_bad_backend_raises(self):
        with pytest.raises(ConfigValidationError, match="backend"):
            StorageConfig(backend="mysql")

    def test_postgres_missing_url_raises(self):
        with pytest.raises(ConfigValidationError, match="postgres_url"):
            StorageConfig(backend="postgres", postgres_url="")

    def test_postgres_with_url_ok(self):
        cfg = StorageConfig(
            backend="postgres", postgres_url="postgresql://localhost/test"
        )
        assert cfg.backend == "postgres"


class TestStorageConfigBackwardCompat:
    def test_config_with_only_path(self):
        cfg = openminion_config_from_dict({"storage": {"path": "/tmp/test.db"}})
        assert cfg.storage.path == "/tmp/test.db"
        assert cfg.storage.backend == "sqlite"

    def test_empty_config(self):
        cfg = openminion_config_from_dict({})
        assert cfg.storage.backend == "sqlite"
        assert cfg.storage.postgres_url == ""


class TestStorageConfigEnvOverride:
    def test_backend_env_override(self, monkeypatch):
        monkeypatch.setenv("OPENMINION_STORAGE_BACKEND", "sqlite")
        cfg = openminion_config_from_dict({"storage": {"backend": "sqlite"}})
        assert cfg.storage.backend == "sqlite"

    def test_postgres_url_env_override(self, monkeypatch):
        monkeypatch.setenv("OPENMINION_STORAGE_BACKEND", "postgres")
        monkeypatch.setenv(
            "OPENMINION_STORAGE_POSTGRES_URL", "postgresql://env-host/db"
        )
        cfg = openminion_config_from_dict({})
        assert cfg.storage.backend == "postgres"
        assert cfg.storage.postgres_url == "postgresql://env-host/db"

    def test_pool_env_override(self, monkeypatch):
        monkeypatch.setenv("OPENMINION_STORAGE_BACKEND", "postgres")
        monkeypatch.setenv(
            "OPENMINION_STORAGE_POSTGRES_URL", "postgresql://localhost/test"
        )
        monkeypatch.setenv("OPENMINION_STORAGE_POSTGRES_POOL_MIN", "2")
        monkeypatch.setenv("OPENMINION_STORAGE_POSTGRES_POOL_MAX", "10")
        cfg = openminion_config_from_dict({})
        assert cfg.storage.postgres_pool_min == 2
        assert cfg.storage.postgres_pool_max == 10

    def test_file_used_when_no_env(self, monkeypatch):
        monkeypatch.delenv("OPENMINION_STORAGE_BACKEND", raising=False)
        monkeypatch.delenv("OPENMINION_STORAGE_POSTGRES_URL", raising=False)
        cfg = openminion_config_from_dict({"storage": {"path": "/tmp/x.db"}})
        assert cfg.storage.backend == "sqlite"
