from dataclasses import dataclass
import tempfile
from pathlib import Path
from tests._csc_fixtures import _csc_install_default_agent


from openminion.base.config import OpenMinionConfig, save_config
from openminion.base.config import (
    BaseModuleConfig,
    ModuleConfigFactory,
)
from openminion.base.config import ConfigManager, ConfigManagerError


def test_config_manager_loads_and_caches() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = OpenMinionConfig()
        _csc_install_default_agent(config, name="cfg-test")
        config_path = tmp_path / "config.json"
        save_config(config, str(config_path))

        manager = ConfigManager.load(
            str(config_path), home_root=config_path.parent.resolve()
        )
        assert (
            manager.base_config.agents[
                next(iter(manager.base_config.agents.keys()))
            ].name
            == "cfg-test"
        )
        assert manager.home_root == config_path.parent.resolve()

        calls = []

        def factory(*, base_config, home_root, data_root):
            calls.append(
                (
                    base_config.agents[next(iter(base_config.agents.keys()))].name,
                    home_root,
                    data_root,
                )
            )
            return {
                "agent": base_config.agents[next(iter(base_config.agents.keys()))].name,
                "root": str(home_root),
            }

        manager.register("sample", factory)
        first = manager.get("sample")
        second = manager.get("sample")
        assert first == second
        assert len(calls) == 1

        manager.reset("sample")
        third = manager.get("sample")
        assert len(calls) == 2
        assert third["agent"] == "cfg-test"


def test_config_manager_unknown_module_raises(fresh_config_manager) -> None:
    manager = fresh_config_manager
    try:
        manager.get("missing")
    except ConfigManagerError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("expected ConfigManagerError")


def test_config_manager_explicit_missing_config_path_raises(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing-config.json"
    try:
        ConfigManager.load(str(missing_path))
    except ConfigManagerError as exc:
        assert str(missing_path) in str(exc)
    else:
        raise AssertionError("expected ConfigManagerError")


def test_config_manager_default_missing_config_path_raises(
    monkeypatch, tmp_path: Path
) -> None:
    home_root = tmp_path / "workspace"
    home_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENMINION_HOME", str(home_root))
    try:
        ConfigManager.load(None)
    except ConfigManagerError as exc:
        assert str(home_root / ".openminion" / "agents.json") in str(exc)
    else:
        raise AssertionError("expected ConfigManagerError")


def test_config_manager_prefers_explicit_relative_config_from_cwd(
    monkeypatch, tmp_path: Path
) -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config, name="cwd-config")
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    config_path = workspace / "test-configs" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    save_config(config, str(config_path))

    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path / "other-home"))

    manager = ConfigManager.load("test-configs/config.json")
    assert manager.config_path == config_path.resolve()
    assert (
        manager.base_config.agents[next(iter(manager.base_config.agents.keys()))].name
        == "cwd-config"
    )


def test_config_manager_explicit_relative_config_ignores_home_root_for_path_lookup(
    monkeypatch, tmp_path: Path
) -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config, name="cwd-wins")
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    config_path = workspace / "configs" / "local.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    save_config(config, str(config_path))

    explicit_home_root = tmp_path / "runtime-home"
    explicit_home_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace)

    manager = ConfigManager.load("configs/local.json", home_root=explicit_home_root)

    assert manager.config_path == config_path.resolve()
    assert manager.home_root == explicit_home_root.resolve()
    assert (
        manager.base_config.agents[next(iter(manager.base_config.agents.keys()))].name
        == "cwd-wins"
    )


def test_config_manager_duplicate_registration_raises(fresh_config_manager) -> None:
    manager = fresh_config_manager

    def factory(*, base_config, home_root, data_root):
        return {"ok": True}

    manager.register("sample", factory)
    try:
        manager.register("sample", factory)
    except ConfigManagerError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("expected ConfigManagerError")


def test_config_interface_split_covers_object_and_factory_shapes() -> None:
    @dataclass
    class SampleModuleConfig:
        module_id: str
        version: str
        home_root: Path | None
        data_root: Path | None

    def factory(*, base_config, home_root, data_root):
        return SampleModuleConfig(
            module_id="sample",
            version="v1",
            home_root=home_root,
            data_root=data_root,
        )

    sample = SampleModuleConfig(
        module_id="sample",
        version="v1",
        home_root=Path("/tmp/runtime"),
        data_root=Path("/tmp/data"),
    )

    assert isinstance(sample, BaseModuleConfig)
    assert isinstance(factory, ModuleConfigFactory)


def test_config_manager_env_includes_runtime_env_values_when_process_unset(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_key = "ECC_101_RUNTIME_ENV_KEY"
    monkeypatch.delenv(runtime_key, raising=False)
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.env = {runtime_key: "from-runtime"}

    manager = ConfigManager(
        base_config=config,
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        config_path=tmp_path / "config.json",
    )

    assert manager.env.get(runtime_key) == "from-runtime"


def test_config_manager_env_prefers_process_env_over_runtime_env(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_key = "ECC_101_RUNTIME_ENV_KEY"
    monkeypatch.setenv(runtime_key, "from-process")
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.env = {runtime_key: "from-runtime"}

    manager = ConfigManager(
        base_config=config,
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        config_path=tmp_path / "config.json",
    )

    assert manager.env.get(runtime_key) == "from-process"
