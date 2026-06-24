from __future__ import annotations

from pathlib import Path

import pytest

from tests._csc_fixtures import _csc_install_default_agent

from openminion.api.runtime import APIRuntime
from openminion.base.config import OpenMinionConfig, save_config


def _create_test_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "api.db")
    save_config(config, str(config_path))
    return config_path


def test_default_mode_is_brain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _create_test_config(tmp_path)
    monkeypatch.setenv("OPENMINION_AGENT_RUNTIME_MODE", "")
    monkeypatch.setenv("OPENMINION_MODULES_ONLY", "true")
    runtime = APIRuntime.from_config_path(str(config_path))
    try:
        assert runtime._runtime_mode == "brain"
        assert runtime._brain_bridge_active is True
    finally:
        runtime.close()


def test_legacy_runtime_mode_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _create_test_config(tmp_path)
    monkeypatch.setenv("OPENMINION_AGENT_RUNTIME_MODE", "legacy")
    monkeypatch.setenv("OPENMINION_MODULES_ONLY", "true")
    runtime = APIRuntime.from_config_path(str(config_path))
    try:
        assert runtime._runtime_mode == "brain"
    finally:
        runtime.close()


def test_legacy_mode_env_does_not_override_module_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _create_test_config(tmp_path)
    monkeypatch.setenv("OPENMINION_AGENT_RUNTIME_MODE", "legacy")
    monkeypatch.setenv("OPENMINION_MODULES_ONLY", "true")
    runtime = APIRuntime.from_config_path(str(config_path))
    try:
        assert runtime._runtime_mode == "brain"
    finally:
        runtime.close()
