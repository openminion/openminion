from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from openminion.base.config import ConfigManager, OpenMinionConfig
from openminion.base.config.env import EnvironmentConfig
from openminion.modules.brain.adapters.a2a import A2actlAdapter
from openminion.services.brain.service import BrainBridgeService
from openminion.services.gateway.config import (
    resolve_memory_capsule_strategy,
    resolve_memory_dynamic_retrieval_enabled,
)
from openminion.services.runtime.bootstrap import build_agent_memory_service
from openminion.services.agent.memory.hello_world import (
    HelloWorldMemoryService,
)
from tests._csc_fixtures import _csc_install_default_agent


def _config_manager(config: OpenMinionConfig, tmp_path: Path) -> ConfigManager:
    return ConfigManager(
        base_config=config,
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        config_path=tmp_path / "config.json",
    )


def test_gateway_config_reads_env_from_config_manager(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENMINION_MEMORY_CAPSULE_STRATEGY", raising=False)
    monkeypatch.delenv("OPENMINION_MEMORY_DYNAMIC_RETRIEVAL_ENABLED", raising=False)
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.env = {
        "OPENMINION_MEMORY_CAPSULE_STRATEGY": "off",
        "OPENMINION_MEMORY_DYNAMIC_RETRIEVAL_ENABLED": "1",
    }
    manager = _config_manager(config, tmp_path)
    agent = SimpleNamespace(_config=config, _config_manager=manager)

    assert resolve_memory_capsule_strategy(agent) == "off"
    assert resolve_memory_dynamic_retrieval_enabled(agent) is True


def test_build_agent_memory_service_prefers_config_manager_env(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENMINION_MEMORY_PROVIDER", "memory_v2_hello_world")
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.memory_enabled = True
    config.runtime.memory_provider = "memory_v2"
    manager = _config_manager(config, tmp_path)

    adapter = build_agent_memory_service(
        config=config,
        agent_id="ecc201-agent",
        memory_root=tmp_path,
        logger=logging.getLogger("ecc201"),
        config_manager=manager,
    )

    assert isinstance(adapter, HelloWorldMemoryService)


def test_brain_bridge_override_uses_environment_config_owner() -> None:
    bridge = object.__new__(BrainBridgeService)
    bridge._env = EnvironmentConfig.from_sources(
        process_env={"ECC201_OVERRIDE": "process"},
        runtime_env={"ECC201_OVERRIDE": "runtime"},
    )

    assert bridge._resolve_override_value("ECC201_OVERRIDE") == "process"


def test_a2a_adapter_build_env_uses_runtime_env_and_process_precedence(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", "/process/data-root")
    cfg = SimpleNamespace(
        runtime=SimpleNamespace(env={"OPENMINION_DATA_ROOT": "/runtime/data-root"})
    )
    env = A2actlAdapter._build_env(cfg)

    assert env.get("OPENMINION_DATA_ROOT") == "/process/data-root"
