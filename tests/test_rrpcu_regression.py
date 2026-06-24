from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from openminion.base.config.base import ConfigError


class TestHomeRootResolution:
    def test_resolve_home_root_from_env_var(self) -> None:
        from openminion.base.config import resolve_home_root

        with patch.dict(
            os.environ, {"OPENMINION_HOME": "/tmp/test-runtime"}, clear=True
        ):
            result = resolve_home_root()
            assert result == Path("/tmp/test-runtime").resolve()

    def test_resolve_home_root_fallback_to_cwd(self) -> None:
        from openminion.base.config import resolve_home_root

        with patch.dict(os.environ, {}, clear=True):
            result = resolve_home_root()
            assert result == Path.cwd().resolve()

    def test_resolve_home_root_from_absolute_config_path(self) -> None:
        from openminion.base.config import resolve_home_root

        with patch.dict(os.environ, {}, clear=True):
            result = resolve_home_root(config_path="/tmp/config/openminion.json")
        assert result == Path("/tmp/config").resolve()

    def test_resolve_home_root_env_overrides_config_path(self) -> None:
        from openminion.base.config import resolve_home_root

        with patch.dict(os.environ, {"OPENMINION_HOME": "/tmp/env-root"}, clear=True):
            result = resolve_home_root(config_path="/tmp/config/openminion.json")
        assert result == Path("/tmp/env-root").resolve()


class TestStoragePathsResolution:
    def test_resolve_storage_paths_integrated(self) -> None:
        from openminion.base.config import resolve_storage_paths

        home_root = Path("/tmp/test-workspace").resolve()
        config_path, storage_path = resolve_storage_paths(home_root)

        assert (
            config_path.resolve()
            == (home_root / ".openminion" / "agents.json").resolve()
        )
        assert (
            storage_path.resolve()
            == (home_root / ".openminion" / "state" / "openminion.db").resolve()
        )

    def test_resolve_module_storage_path(self) -> None:
        from openminion.base.config import resolve_module_storage_path

        home_root = Path("/tmp/workspace").resolve()

        path = resolve_module_storage_path(home_root, "retrieve")
        assert path == home_root / ".openminion" / "retrieve" / "retrieve.db"

        path = resolve_module_storage_path(home_root, "telemetry", filename="events.db")
        assert path == home_root / ".openminion" / "telemetry" / "events.db"

        path = resolve_module_storage_path(home_root, "memory", subdir="vectors")
        assert path == home_root / ".openminion" / "memory" / "vectors" / "memory.db"


class TestHomePathsBootstrap:
    def test_bootstrap_home_paths_default_integrated(self) -> None:
        from openminion.base.config import bootstrap_home_paths

        with patch.dict(os.environ, {}, clear=True):
            paths = bootstrap_home_paths()

        assert paths.path_mode == "integrated_runtime"
        assert paths.path_source in {"default_integrated", "fallback"}
        assert paths.home_root == Path.cwd().resolve()
        assert ".openminion" in str(paths.config_path)
        assert ".openminion" in str(paths.storage_path)

    def test_bootstrap_home_paths_from_env(self) -> None:
        from openminion.base.config import bootstrap_home_paths

        with patch.dict(os.environ, {"OPENMINION_HOME": "/tmp/env-root"}, clear=True):
            paths = bootstrap_home_paths()

        assert paths.home_root == Path("/tmp/env-root").resolve()
        assert paths.path_source == "env_var"
        assert paths.path_mode == "integrated_runtime"

    def test_bootstrap_home_paths_explicit_workspace_beats_env(self) -> None:
        from openminion.base.config import bootstrap_home_paths

        with patch.dict(os.environ, {"OPENMINION_HOME": "/tmp/env-root"}, clear=True):
            paths = bootstrap_home_paths(workspace_root="/tmp/explicit-root")

        assert paths.home_root == Path("/tmp/explicit-root").resolve()
        assert paths.path_source == "explicit_workspace"
        assert paths.path_mode == "integrated_runtime"

    def test_bootstrap_home_paths_standalone_mode(self) -> None:
        from openminion.base.config import bootstrap_home_paths

        with patch.dict(
            os.environ,
            {"OPENMINION_MODULE_STANDALONE": "true"},
            clear=True,
        ):
            paths = bootstrap_home_paths()

        assert paths.path_mode == "module_standalone"
        assert paths.path_source == "env_standalone"

    def test_home_paths_to_debug_dict(self) -> None:
        from openminion.base.config import HomePaths

        paths = HomePaths(
            home_root=Path("/tmp/runtime").resolve(),
            data_root=Path("/tmp/runtime/.openminion").resolve(),
            config_path=Path("/tmp/runtime/.openminion/agents.json").resolve(),
            storage_path=Path("/tmp/runtime/.openminion/state/db.sqlite").resolve(),
            path_mode="integrated_runtime",
            path_source="env_var",
        )

        debug = paths.to_debug_dict()
        resolved_root = str(Path("/tmp/runtime").resolve())
        assert debug["home_root"] == resolved_root
        assert debug["config_path"] == f"{resolved_root}/.openminion/agents.json"
        assert debug["storage_path"] == f"{resolved_root}/.openminion/state/db.sqlite"
        assert debug["path_mode"] == "integrated_runtime"
        assert debug["path_source"] == "env_var"


class TestConfigManagerResolution:
    def test_config_manager_default_does_not_nest_data_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openminion.base.config.manager import ConfigManager
        from openminion.base.config import OpenMinionConfig, save_config

        monkeypatch.chdir(tmp_path)
        config_path = tmp_path / ".openminion" / "agents.json"
        save_config(OpenMinionConfig(), str(config_path))
        with patch.dict(os.environ, {}, clear=True):
            manager = ConfigManager.load()

        assert manager.home_root == tmp_path.resolve()
        assert manager.data_root == (tmp_path / ".openminion").resolve()
        assert (
            manager.config_path == (tmp_path / ".openminion" / "agents.json").resolve()
        )


class TestConfigPathResolution:
    def test_resolve_config_path_with_home_root(self) -> None:
        from openminion.base.config import resolve_config_path

        home_root = Path("/tmp/workspace").resolve()
        with patch("pathlib.Path.cwd", return_value=Path("/tmp/shell").resolve()):
            result = resolve_config_path("config/openminion.json", home_root=home_root)

        assert result == Path("/tmp/shell/config/openminion.json").resolve()

    def test_resolve_config_path_absolute(self) -> None:
        from openminion.base.config import resolve_config_path

        result = resolve_config_path("/etc/openminion/config.json")
        assert result == Path("/etc/openminion/config.json").resolve()

    def test_resolve_config_path_legacy_fallback(self) -> None:
        from openminion.base.config import resolve_config_path, DEFAULT_CONFIG_PATH

        with patch.dict(os.environ, {}, clear=True):
            result = resolve_config_path(None)
            assert result == (Path.home() / DEFAULT_CONFIG_PATH).resolve()


class TestRetrieveConfigPathResolution:
    def test_retrieve_resolve_home_root(self) -> None:
        from openminion.modules.retrieve.config import resolve_home_root

        with patch.dict(
            os.environ, {"OPENMINION_HOME": "/tmp/retrieve-test"}, clear=True
        ):
            result = resolve_home_root()
            assert result == Path("/tmp/retrieve-test").resolve()

    def test_retrieve_is_standalone_mode(self) -> None:
        from openminion.modules.retrieve.config import is_standalone_mode

        with patch.dict(
            os.environ, {"OPENMINION_MODULE_STANDALONE": "true"}, clear=True
        ):
            assert is_standalone_mode() is True

        with patch.dict(
            os.environ, {"OPENMINION_MODULE_STANDALONE": "false"}, clear=True
        ):
            assert is_standalone_mode() is False

        with patch.dict(os.environ, {}, clear=True):
            assert is_standalone_mode() is False

    def test_retrieve_default_storage_paths_integrated(self) -> None:
        from openminion.modules.retrieve.config import get_default_storage_paths

        home_root = Path("/tmp/workspace").resolve()
        sqlite_path, blob_root = get_default_storage_paths(home_root)

        assert sqlite_path == home_root / ".openminion" / "retrieve" / "retrieve.db"
        assert blob_root == home_root / ".openminion" / "retrieve" / "blobs"

    def test_retrieve_default_storage_paths_standalone(self) -> None:
        from openminion.modules.retrieve.config import get_default_storage_paths

        with patch.dict(
            os.environ, {"OPENMINION_MODULE_STANDALONE": "true"}, clear=True
        ):
            sqlite_path, blob_root = get_default_storage_paths()

        assert sqlite_path == Path.home() / ".retrievectl" / "retrievectl.db"
        assert blob_root == Path.home() / ".retrievectl"

    def test_retrieve_load_config_path_metadata(self) -> None:
        from openminion.modules.retrieve.config import load_config

        with patch.dict(os.environ, {}, clear=True):
            home_root = Path("/tmp/retrieve-config-test")
            config = load_config({}, home_root=home_root)

        assert config.storage.path_mode == "integrated_runtime"
        assert config.storage.path_source in ("inline_dict", "explicit_home_root")
        assert ".openminion" in str(config.storage.sqlite_path)


class TestDebugSurfacePathMetadata:
    def test_create_path_debug_payload(self) -> None:
        from openminion.services.diagnostics.debug import (
            create_path_debug_payload,
            DebugStatus,
        )

        payload = create_path_debug_payload(
            module="openminion-retrieve",
            resolved_path=Path("/tmp/workspace/.openminion/retrieve.db"),
            path_mode="integrated_runtime",
            path_source="default_integrated",
            status=DebugStatus.OK,
            sqlite_path="/tmp/workspace/.openminion/retrieve.db",
            blob_root="/tmp/workspace/.openminion/blobs",
        )

        assert payload.module == "openminion-retrieve"
        assert payload.resolved_path == "/tmp/workspace/.openminion/retrieve.db"
        assert payload.path_mode == "integrated_runtime"
        assert payload.path_source == "default_integrated"
        assert payload.status == DebugStatus.OK

        debug_dict = payload.to_dict()
        assert debug_dict["resolved_path"] == "/tmp/workspace/.openminion/retrieve.db"
        assert debug_dict["path_mode"] == "integrated_runtime"
        assert debug_dict["path_source"] == "default_integrated"
        assert (
            debug_dict["details"]["sqlite_path"]
            == "/tmp/workspace/.openminion/retrieve.db"
        )

    def test_module_debug_payload_path_fields_optional(self) -> None:
        from openminion.services.diagnostics.debug import (
            ModuleDebugPayload,
            DebugStatus,
            WiringSource,
        )

        payload = ModuleDebugPayload(
            module="test-module",
            status=DebugStatus.OK,
            mode="test",
            wiring_source=WiringSource.REAL,
        )

        debug_dict = payload.to_dict()
        assert "resolved_path" not in debug_dict
        assert "path_mode" not in debug_dict
        assert "path_source" not in debug_dict


class TestNegativePathScenarios:
    def test_no_home_leakage_in_integrated_mode(self) -> None:
        from openminion.modules.retrieve.config import get_default_storage_paths

        home_root = Path("/tmp/isolated-workspace").resolve()

        with patch.dict(os.environ, {}, clear=True):
            sqlite_path, blob_root = get_default_storage_paths(home_root)

        assert str(sqlite_path).startswith(str(home_root))
        assert str(blob_root).startswith(str(home_root))
        assert ".openminion" in str(sqlite_path)
        assert ".openminion" in str(blob_root)
        assert str(Path.home()) not in str(sqlite_path)
        assert str(Path.home()) not in str(blob_root)

    def test_explicit_override_allowed_in_hard_mode(self) -> None:
        from openminion.modules.retrieve.config import load_config

        home_root = Path("/tmp/workspace").resolve()

        with patch.dict(
            os.environ,
            {
                "OPENMINION_DATA_ROOT": "/tmp/workspace/.openminion",
                "OPENMINION_DATA_ROOT_ENFORCEMENT": "hard",
            },
            clear=True,
        ):
            cfg = load_config(
                {
                    "version": 1,
                    "retrievectl": {
                        "storage": {
                            "sqlite_path": "/custom/path/retrieve.db",
                            "blob_root": "/custom/blobs",
                        }
                    },
                },
                home_root=home_root,
            )
        assert cfg.storage.sqlite_path == Path("/custom/path/retrieve.db").resolve()
        assert cfg.storage.blob_root == Path("/custom/blobs").resolve()


class TestDataRootEnforcement:
    def test_generated_root_relative_under_runtime(self) -> None:
        from openminion.base.generated_paths import resolve_generated_root

        with patch.dict(
            os.environ,
            {
                "OPENMINION_DATA_ROOT": "/tmp/om-data",
                "OPENMINION_GENERATED_ROOT": "scratch",
            },
            clear=True,
        ):
            result = resolve_generated_root()

        assert result == Path("/tmp/om-data/runtime/scratch").resolve()

    def test_generated_root_outside_hard_mode(self) -> None:
        from openminion.base.generated_paths import resolve_generated_root

        with patch.dict(
            os.environ,
            {
                "OPENMINION_DATA_ROOT": "/tmp/om-data",
                "OPENMINION_GENERATED_ROOT": "/tmp/outside",
                "OPENMINION_DATA_ROOT_ENFORCEMENT": "hard",
            },
            clear=True,
        ):
            with pytest.raises(ConfigError):
                resolve_generated_root()

    def test_generated_root_outside_soft_mode_rewrite(self) -> None:
        from openminion.base.generated_paths import resolve_generated_root

        with patch.dict(
            os.environ,
            {
                "OPENMINION_DATA_ROOT": "/tmp/om-data",
                "OPENMINION_GENERATED_ROOT": "/tmp/outside",
                "OPENMINION_DATA_ROOT_ENFORCEMENT": "soft",
            },
            clear=True,
        ):
            with pytest.warns(RuntimeWarning):
                result = resolve_generated_root()

        assert result == Path("/tmp/om-data/runtime/outside").resolve()

    def test_database_path_outside_soft_mode(self) -> None:
        from openminion.modules.storage.runtime.sqlite import resolve_database_path

        with patch.dict(
            os.environ,
            {
                "OPENMINION_DATA_ROOT": "/tmp/om-data",
                "OPENMINION_DATA_ROOT_ENFORCEMENT": "soft",
            },
            clear=True,
        ):
            result = resolve_database_path("/tmp/outside.db")

        assert result == Path("/tmp/outside.db").resolve()


class TestDataRootModuleEnforcement:
    def test_artifact_paths_outside_hard_mode(self) -> None:
        from openminion.modules.artifact.config import load_config

        with patch.dict(
            os.environ,
            {
                "OPENMINION_HOME": "/tmp/workspace",
                "OPENMINION_DATA_ROOT": "/tmp/om-data",
                "OPENMINION_DATA_ROOT_ENFORCEMENT": "hard",
            },
            clear=True,
        ):
            with pytest.raises(ConfigError):
                load_config(
                    {
                        "artifactctl": {
                            "blob_store": {"root_dir": "/tmp/outside"},
                            "index": {"sqlite_path": "/tmp/outside/index.db"},
                        }
                    }
                )

    def test_skill_paths_outside_hard_mode(self) -> None:
        from openminion.modules.skill.config import load_config

        with patch.dict(
            os.environ,
            {"OPENMINION_DATA_ROOT_ENFORCEMENT": "hard"},
            clear=True,
        ):
            with pytest.raises(ConfigError):
                load_config(
                    {"skill": {"sqlite_path": "/tmp/outside.db"}},
                    home_root=Path("/tmp/workspace"),
                    env={"OPENMINION_DATA_ROOT": "/tmp/om-data"},
                )

    def test_skill_paths_outside_soft_mode(self) -> None:
        from openminion.modules.skill.config import load_config

        with patch.dict(
            os.environ,
            {"OPENMINION_DATA_ROOT_ENFORCEMENT": "soft"},
            clear=True,
        ):
            with pytest.warns(RuntimeWarning):
                cfg = load_config(
                    {"skill": {"sqlite_path": "/tmp/outside.db"}},
                    home_root=Path("/tmp/workspace"),
                    env={"OPENMINION_DATA_ROOT": "/tmp/om-data"},
                )

        assert cfg.sqlite_path == str(Path("/tmp/outside.db").resolve())

    def test_retrieve_paths_outside_hard_mode_allow_override(self) -> None:
        from openminion.modules.retrieve.config import load_config

        with patch.dict(
            os.environ,
            {"OPENMINION_DATA_ROOT_ENFORCEMENT": "hard"},
            clear=True,
        ):
            cfg = load_config(
                {
                    "version": 1,
                    "retrievectl": {
                        "storage": {
                            "sqlite_path": "/tmp/outside.db",
                            "blob_root": "/tmp/outside",
                        }
                    },
                },
                home_root=Path("/tmp/workspace"),
                env={"OPENMINION_DATA_ROOT": "/tmp/om-data"},
            )
        assert cfg.storage.sqlite_path == Path("/tmp/outside.db").resolve()
        assert cfg.storage.blob_root == Path("/tmp/outside").resolve()

    def test_memory_paths_outside_hard_mode_allow_override(
        self, tmp_path: Path
    ) -> None:
        from openminion.modules.memory.config import load_config

        config_path = tmp_path / "memory.yaml"
        config_path.write_text(
            "\n".join(
                [
                    "version: 1",
                    "memctl:",
                    "  store:",
                    "    backend: sqlite",
                    "    sqlite_path: /tmp/outside.db",
                    "    sqlite: {}",
                    "  defaults:",
                    "    confidence: {}",
                    "  promotion:",
                    "    allowlisted_auto_rules: []",
                    "  retrieval: {}",
                    "  retention: {}",
                ]
            ),
            encoding="utf-8",
        )

        with patch.dict(
            os.environ,
            {"OPENMINION_DATA_ROOT_ENFORCEMENT": "hard"},
            clear=True,
        ):
            cfg = load_config(
                str(config_path),
                home_root=Path("/tmp/workspace"),
                env={"OPENMINION_DATA_ROOT": "/tmp/om-data"},
            )
        assert cfg.store.sqlite_path == Path("/tmp/outside.db").resolve()

    def test_telemetry_paths_outside_hard_mode(self) -> None:
        from openminion.modules.telemetry.service import resolve_telemetry_db_path

        with patch.dict(
            os.environ,
            {
                "OPENMINION_HOME": "/tmp/workspace",
                "OPENMINION_DATA_ROOT": "/tmp/om-data",
                "OPENMINION_DATA_ROOT_ENFORCEMENT": "hard",
            },
            clear=True,
        ):
            with pytest.raises(ConfigError):
                resolve_telemetry_db_path(
                    db_path="/tmp/outside.db",
                    home_root="/tmp/workspace",
                )

    def test_registry_paths_outside_hard_mode(self) -> None:
        from openminion.modules.registry.config import config_from_dict

        with patch.dict(
            os.environ,
            {"OPENMINION_DATA_ROOT_ENFORCEMENT": "hard"},
            clear=True,
        ):
            with pytest.raises(ConfigError):
                config_from_dict(
                    {
                        "agentregctl": {
                            "manifest_path": "/tmp/outside.yaml",
                            "store": {"sqlite_path": "/tmp/outside.db"},
                        }
                    },
                    home_root=Path("/tmp/workspace"),
                    env={"OPENMINION_DATA_ROOT": "/tmp/om-data"},
                )

    def test_controlplane_paths_outside_hard_mode(self) -> None:
        from openminion.modules.controlplane.config import load_config

        with patch.dict(
            os.environ,
            {"OPENMINION_DATA_ROOT_ENFORCEMENT": "hard"},
            clear=True,
        ):
            with pytest.raises(ConfigError):
                load_config(
                    {"sqlite_path": "/tmp/outside.db"},
                    home_root=Path("/tmp/workspace"),
                    env={"OPENMINION_DATA_ROOT": "/tmp/om-data"},
                )

    def test_a2a_paths_outside_hard_mode(self) -> None:
        from openminion.modules.a2a.config import load_config

        with patch.dict(
            os.environ,
            {
                "OPENMINION_HOME": "/tmp/workspace",
                "OPENMINION_DATA_ROOT": "/tmp/om-data",
                "OPENMINION_DATA_ROOT_ENFORCEMENT": "hard",
            },
            clear=True,
        ):
            with pytest.raises(ConfigError):
                load_config(
                    {
                        "storage": {"state": {"path": "/tmp/outside.db"}},
                        "artifacts": {"root": "/tmp/outside"},
                    }
                )
