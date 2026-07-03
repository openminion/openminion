from __future__ import annotations

import os
from pathlib import Path

import yaml

from openminion.base.constants import OPENMINION_HOME_ENV
from openminion.modules.memory.config import (
    load_config,
    _resolve_config_path,
    _parse_store,
    MEMCTL_CONFIG_ENV,
)


def create_config_file(path: Path, sqlite_path: str) -> None:
    config_content = {
        "version": 1,
        "memctl": {
            "store": {
                "backend": "sqlite",
                "sqlite_path": sqlite_path,
            },
            "defaults": {
                "confidence": {
                    "user_said": 0.8,
                    "tool_output": 0.9,
                }
            },
            "promotion": {},
            "retrieval": {},
            "retention": {},
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(config_content))


def _without_openminion_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        OPENMINION_HOME_ENV,
        OPENMINION_HOME_ENV,
        "OPENMINION_DATA_ROOT",
    ):
        env.pop(key, None)
    return env


class TestHomeRootPathResolution:
    def test_integrated_mode_home_root_provided(self, tmp_path: Path):
        config_file = tmp_path / ".openminion" / "memory.yaml"
        create_config_file(config_file, "memory.db")

        cfg = load_config(
            config_file, home_root=tmp_path, env=_without_openminion_env()
        )

        assert cfg.path_mode == "integrated_runtime"
        assert cfg.path_source == "home_root"
        expected_db_path = tmp_path / ".openminion" / "memory.db"
        assert cfg.store.sqlite_path == expected_db_path

    def test_standalone_mode_no_home_root(self, tmp_path: Path):
        config_file = tmp_path / "memory.yaml"
        create_config_file(config_file, "memory.db")

        env = _without_openminion_env()

        cfg = load_config(config_file, env=env)

        assert cfg.path_mode == "module_standalone"
        assert cfg.path_source == "explicit_config"

    def test_explicit_config_path(self, tmp_path: Path):
        config_file = tmp_path / "custom" / "memory.yaml"
        create_config_file(config_file, "/absolute/path/to/db.sqlite")

        cfg = load_config(config_file, env=_without_openminion_env())
        assert cfg.path_source == "explicit_config"

    def test_relative_path_with_home_root(self, tmp_path: Path):
        config_file = tmp_path / "config" / "memory.yaml"
        create_config_file(config_file, "./data/memory.db")

        cfg = load_config(
            config_file, home_root=tmp_path, env=_without_openminion_env()
        )

        expected_path = tmp_path / ".openminion" / "data" / "memory.db"
        assert cfg.store.sqlite_path == expected_path

    def test_absolute_path_unchanged(self, tmp_path: Path):
        config_file = tmp_path / "memory.yaml"
        absolute_db_path = "/var/lib/openminion/memory.db"
        create_config_file(config_file, absolute_db_path)

        cfg = load_config(
            config_file, home_root=tmp_path, env=_without_openminion_env()
        )

        assert str(cfg.store.sqlite_path).endswith(absolute_db_path)


class TestConfigPathResolution:
    def test_resolve_config_path_with_home_root(self, tmp_path: Path):
        env = {}
        result = _resolve_config_path("config/memory.yaml", env, home_root=tmp_path)
        expected = tmp_path / "config" / "memory.yaml"
        assert result == expected

    def test_resolve_config_path_absolute(self, tmp_path: Path):
        env = {}
        absolute_path = tmp_path / "absolute" / "memory.yaml"
        absolute_path.parent.mkdir(parents=True)

        result = _resolve_config_path(str(absolute_path), env, home_root=Path("/other"))
        assert result == absolute_path

    def test_resolve_config_path_legacy(self):
        env = {}
        result = _resolve_config_path("memory.yaml", env)
        expected = Path.home() / "memory.yaml"
        assert result == expected


class TestStorePathResolution:
    def test_parse_store_with_home_root(self, tmp_path: Path):
        env = {}
        store_config = {
            "backend": "sqlite",
            "sqlite_path": "./db/memory.sqlite",
        }

        result = _parse_store(store_config, env, home_root=tmp_path)

        expected_path = tmp_path / "db" / "memory.sqlite"
        assert result.sqlite_path == expected_path

    def test_parse_store_absolute_path(self):
        env = {}
        store_config = {
            "backend": "sqlite",
            "sqlite_path": "/var/db/memory.sqlite",
        }

        result = _parse_store(store_config, env, home_root=Path("/other"))

        assert str(result.sqlite_path).endswith("/var/db/memory.sqlite")


class TestEnvironmentVariableResolution:
    def test_home_root_from_env_var(self, tmp_path: Path):
        config_file = tmp_path / "memory.yaml"
        create_config_file(config_file, "data/memory.db")

        home_root = tmp_path / "home_root"
        home_root.mkdir()

        env = os.environ.copy()
        env[OPENMINION_HOME_ENV] = str(home_root)

        cfg = load_config(config_file, env=env)

        assert cfg.path_mode == "integrated_runtime"
        assert cfg.path_source == "env_var"

    def test_memctl_config_env_var(self, tmp_path: Path):
        config_file = tmp_path / "custom" / "memory.yaml"
        create_config_file(config_file, "memory.db")

        env = os.environ.copy()
        env[MEMCTL_CONFIG_ENV] = str(config_file)

        cfg = load_config(env=env)
        assert cfg.store.backend == "sqlite"


class TestDebugProviderPathMetadata:
    def test_debug_info_includes_path_mode(self, tmp_path: Path):
        from openminion.modules.memory.diagnostics.debug import MemoryDebugProvider

        config_file = tmp_path / "memory.yaml"
        create_config_file(config_file, "memory.db")

        provider = MemoryDebugProvider()
        info = provider.get_debug_info(
            config_path=str(config_file),
            home_root=tmp_path,
        )

        assert info.path_mode == "integrated_runtime"

    def test_debug_info_includes_resolved_paths(self, tmp_path: Path):
        from openminion.modules.memory.diagnostics.debug import MemoryDebugProvider

        config_file = tmp_path / "memory.yaml"
        create_config_file(config_file, "data/memory.db")

        provider = MemoryDebugProvider()
        info = provider.get_debug_info(
            config_path=str(config_file),
            home_root=tmp_path,
        )

        assert info.sqlite_path is not None
        assert "data" in info.sqlite_path

    def test_path_diagnostics_resolution_chain(self):
        from openminion.modules.memory.diagnostics.debug import MemoryDebugProvider

        provider = MemoryDebugProvider()
        diagnostics = provider.get_path_diagnostics()

        assert "environment" in diagnostics
        assert "OPENMINION_HOME" in diagnostics["environment"]
        assert "resolution_chain" in diagnostics
