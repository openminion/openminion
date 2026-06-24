from __future__ import annotations

import os

import pytest

from openminion.base.config.core import StorageConfig, ConfigValidationError
from openminion.base.config.parser import openminion_config_from_dict
from openminion.modules.storage.runtime.validation import (
    StorageConfigError,
    validate_storage_config,
)

pytestmark = pytest.mark.postgres


class TestSQLiteWorkflow:
    def test_default_sqlite_config_works(self, tmp_path):
        cfg = openminion_config_from_dict(
            {"storage": {"path": str(tmp_path / "test.db")}}
        )
        assert cfg.storage.backend == "sqlite"
        assert cfg.storage.record_backend() == "record.sqlite"
        assert cfg.storage.record_backend_options() == {}

    def test_sqlite_config_explicit(self, tmp_path):
        cfg = openminion_config_from_dict(
            {
                "storage": {
                    "path": str(tmp_path / "test.db"),
                    "backend": "sqlite",
                }
            }
        )
        assert cfg.storage.backend == "sqlite"
        assert cfg.storage.record_backend() == "record.sqlite"

    def test_sqlite_build_runtime_storage(self, tmp_path):
        from openminion.modules.storage.runtime.context import build_runtime_storage

        db_path = tmp_path / "runtime.db"
        ctx = build_runtime_storage(db_path)
        assert ctx.record_store is not None
        assert ctx.sessions is not None
        assert ctx.idempotency is not None
        ctx.close()

    def test_sqlite_config_serialization_roundtrip(self, tmp_path):
        cfg = openminion_config_from_dict(
            {"storage": {"path": str(tmp_path / "test.db")}}
        )
        data = cfg.to_dict()
        assert data["storage"]["backend"] == "sqlite"
        cfg2 = openminion_config_from_dict(data)
        assert cfg2.storage.backend == "sqlite"
        assert cfg2.storage.path == cfg.storage.path


class TestPostgresConfigValidation:
    def test_postgres_missing_url_raises_config_validation_error(self):
        with pytest.raises(ConfigValidationError, match="postgres_url"):
            StorageConfig(backend="postgres", postgres_url="")

    def test_unknown_backend_raises_config_validation_error(self):
        with pytest.raises(ConfigValidationError, match="backend"):
            StorageConfig(backend="mysql")

    def test_postgres_storage_config_error_on_unreachable_db(self):
        with pytest.raises(StorageConfigError):
            validate_storage_config(
                "postgres",
                "postgresql://localhost:9/nonexistent_db_openminion_test",
                check_connection=True,
                check_migrations=False,
            )

    def test_postgres_validation_no_credentials_in_error(self):
        url = "postgresql://user:MySecretPassword@localhost:9/db"
        with pytest.raises(StorageConfigError) as exc_info:
            validate_storage_config(
                "postgres",
                url,
                check_connection=True,
                check_migrations=False,
            )
        assert "MySecretPassword" not in str(exc_info.value)

    def test_postgres_build_runtime_storage_bad_url_raises(self, tmp_path):
        from openminion.modules.storage.runtime.context import build_runtime_storage

        with pytest.raises(StorageConfigError):
            build_runtime_storage(
                tmp_path / "test.db",
                record_backend="record.postgres",
                record_backend_options={
                    "url": "postgresql://localhost:9/nonexistent_test"
                },
            )


class TestEnvVarWorkflow:
    def test_env_backend_overrides_file(self, monkeypatch):
        monkeypatch.setenv("OPENMINION_STORAGE_BACKEND", "sqlite")
        cfg = openminion_config_from_dict({"storage": {"backend": "sqlite"}})
        assert cfg.storage.backend == "sqlite"

    def test_env_postgres_url_override(self, monkeypatch):
        monkeypatch.setenv("OPENMINION_STORAGE_BACKEND", "postgres")
        monkeypatch.setenv(
            "OPENMINION_STORAGE_POSTGRES_URL", "postgresql://env-host/testdb"
        )
        cfg = openminion_config_from_dict({})
        assert cfg.storage.backend == "postgres"
        assert cfg.storage.postgres_url == "postgresql://env-host/testdb"
        assert cfg.storage.record_backend() == "record.postgres"
        opts = cfg.storage.record_backend_options()
        assert opts["url"] == "postgresql://env-host/testdb"

    def test_env_pool_settings_override(self, monkeypatch):
        monkeypatch.setenv("OPENMINION_STORAGE_BACKEND", "postgres")
        monkeypatch.setenv(
            "OPENMINION_STORAGE_POSTGRES_URL", "postgresql://localhost/test"
        )
        monkeypatch.setenv("OPENMINION_STORAGE_POSTGRES_POOL_MIN", "3")
        monkeypatch.setenv("OPENMINION_STORAGE_POSTGRES_POOL_MAX", "15")
        cfg = openminion_config_from_dict({})
        assert cfg.storage.postgres_pool_min == 3
        assert cfg.storage.postgres_pool_max == 15

    def test_no_env_uses_file_config(self, monkeypatch):
        monkeypatch.delenv("OPENMINION_STORAGE_BACKEND", raising=False)
        monkeypatch.delenv("OPENMINION_STORAGE_POSTGRES_URL", raising=False)
        cfg = openminion_config_from_dict({"storage": {"path": "/tmp/mydb.db"}})
        assert cfg.storage.backend == "sqlite"
        assert cfg.storage.path == "/tmp/mydb.db"


@pytest.mark.postgres
class TestPostgresWorkflow:
    @pytest.fixture
    def pg_url(self):
        url = os.environ.get("OPENMINION_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("OPENMINION_TEST_POSTGRES_URL not set")
        return url

    def test_postgres_config_valid(self, pg_url):
        cfg = StorageConfig(backend="postgres", postgres_url=pg_url)
        assert cfg.record_backend() == "record.postgres"
        opts = cfg.record_backend_options()
        assert opts["url"] == pg_url

    def test_postgres_startup_validation_passes(self, pg_url):
        validate_storage_config(
            "postgres",
            pg_url,
            check_connection=True,
            check_migrations=False,
        )

    def test_postgres_build_runtime_storage(self, tmp_path, pg_url):
        from openminion.modules.storage.runtime.context import build_runtime_storage

        ctx = build_runtime_storage(
            tmp_path / "placeholder.db",
            record_backend="record.postgres",
            record_backend_options={"url": pg_url},
        )
        assert ctx.record_store is not None
        assert ctx.sessions is not None
        assert ctx.idempotency is not None
        ctx.close()
