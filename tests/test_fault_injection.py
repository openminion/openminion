from __future__ import annotations

import os
import sys
from unittest import mock

import pytest

from openminion.base.config import OpenMinionConfig, save_config
from tests._csc_fixtures import _csc_install_default_agent
from tests.helpers import LaneAssertionError, NoLegacyTestContext


def _write_echo_config(tmp_path) -> str:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "api.db")
    save_config(config, str(config_path))
    return str(config_path)


def test_strict_mode_with_missing_brain_import(tmp_path) -> None:
    original_modules = dict(sys.modules)
    brain_modules_to_hide = [
        "openminion.services.brain.service",
        "openminion_brain",
    ]
    for mod in list(sys.modules):
        if any(mod.startswith(hide) for hide in brain_modules_to_hide):
            del sys.modules[mod]

    try:
        with mock.patch.dict(
            os.environ, {"OPENMINION_AGENT_RUNTIME_MODE": "brain"}, clear=False
        ):
            try:
                from openminion.api.runtime import APIRuntime

                runtime = APIRuntime.from_config_path(_write_echo_config(tmp_path))
                runtime.close()
            except RuntimeError as exc:
                assert "brain" in str(exc).lower()
    finally:
        sys.modules.clear()
        sys.modules.update(original_modules)


def test_no_implicit_fallback_on_config_error(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{invalid json")

    with mock.patch.dict(
        os.environ, {"OPENMINION_AGENT_RUNTIME_MODE": "brain"}, clear=False
    ):
        with pytest.raises(Exception) as exc_info:
            from openminion.base.config import load_config

            load_config(str(config_path))
        assert "legacy" not in str(exc_info.value).lower()


def test_legacy_runtime_mode_env_is_ignored(tmp_path) -> None:
    with mock.patch.dict(
        os.environ, {"OPENMINION_AGENT_RUNTIME_MODE": "legacy"}, clear=False
    ):
        from openminion.api.runtime import APIRuntime

        runtime = APIRuntime.from_config_path(_write_echo_config(tmp_path))
        try:
            assert runtime._runtime_mode == "brain"
        finally:
            runtime.close()


def test_no_legacy_context_rejects_legacy_env() -> None:
    with mock.patch.dict(
        os.environ, {"OPENMINION_AGENT_RUNTIME_MODE": "legacy"}, clear=False
    ):
        with pytest.raises(LaneAssertionError) as exc_info:
            with NoLegacyTestContext(source="test_strict", strict=True):
                pass
        assert "legacy" in str(exc_info.value).lower()


def test_no_legacy_context_accepts_module_env() -> None:
    with mock.patch.dict(
        os.environ, {"OPENMINION_AGENT_RUNTIME_MODE": "brain"}, clear=False
    ):
        with NoLegacyTestContext(source="test_module", strict=True) as ctx:
            assert ctx.source == "test_module"
            assert ctx.strict


def test_brain_mode_env_is_applied() -> None:
    with mock.patch.dict(
        os.environ, {"OPENMINION_AGENT_RUNTIME_MODE": "brain"}, clear=False
    ):
        assert os.environ.get("OPENMINION_AGENT_RUNTIME_MODE") == "brain"


def test_runtime_error_messages_are_actionable(tmp_path) -> None:
    with mock.patch.dict(
        os.environ, {"OPENMINION_AGENT_RUNTIME_MODE": "brain"}, clear=False
    ):
        try:
            from openminion.api.runtime import APIRuntime

            runtime = APIRuntime.from_config_path(_write_echo_config(tmp_path))
            assert runtime._runtime_mode == "brain"
            runtime.close()
        except RuntimeError as exc:
            assert "brain" in str(exc).lower()
