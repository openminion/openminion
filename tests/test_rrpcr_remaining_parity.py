from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch


_WORKSPACE = Path(__file__).resolve().parents[2]
for _module in ("openminion-controlplane",):
    _src = _WORKSPACE / _module / "src"
    if _src.exists():
        _src_str = str(_src)
        if _src_str not in sys.path:
            sys.path.insert(0, _src_str)


def test_skill_config_defaults_to_home_root(tmp_path: Path) -> None:
    from openminion.modules.skill.config import load_config

    cfg = load_config({}, home_root=tmp_path)

    assert cfg.path_mode == "integrated_runtime"
    assert cfg.path_source == "default_integrated"
    assert str(cfg.sqlite_path).startswith(str(tmp_path))
    assert ".openminion/skill" in str(cfg.sqlite_path)


def test_skill_config_explicit_override_with_home_root(tmp_path: Path) -> None:
    from openminion.modules.skill.config import load_config

    cfg = load_config(
        {
            "skill": {
                "sqlite_path": "custom/skills.sqlite3",
                "blob_root": "custom/blobs",
                "fallback_root": "custom/fallback",
            }
        },
        home_root=tmp_path,
    )

    assert cfg.path_source == "explicit_override"
    assert str(cfg.sqlite_path).startswith(str(tmp_path))
    assert str(cfg.blob_root).startswith(str(tmp_path))
    assert str(cfg.fallback_root).startswith(str(tmp_path))


def test_create_skill_adapter_passes_home_root(tmp_path: Path) -> None:
    from openminion.modules.brain.adapters.factory import create_skill_adapter

    adapter = create_skill_adapter(mode="auto", home_root=tmp_path)
    assert adapter is not None
    assert getattr(adapter.config, "path_mode", None) == "integrated_runtime"
    assert str(getattr(adapter.config, "sqlite_path", "")).startswith(str(tmp_path))
    adapter.close()


def test_registry_config_defaults_to_home_root(tmp_path: Path) -> None:
    from openminion.modules.registry.config import load_config

    cfg = load_config(path="missing-registry.yaml", home_root=tmp_path)

    assert cfg.store.path_mode == "integrated_runtime"
    assert cfg.store.path_source == "default_integrated"
    assert str(cfg.store.sqlite_path).startswith(str(tmp_path))
    assert ".openminion/registry" in str(cfg.store.sqlite_path)


def test_registry_config_explicit_override(tmp_path: Path) -> None:
    from openminion.modules.registry.config import config_from_dict

    cfg = config_from_dict(
        {"sqlite_path": "custom/registry.db", "manifest_path": "custom/agents.yaml"},
        home_root=tmp_path,
    )

    assert cfg.store.path_source == "explicit_override"
    assert str(cfg.store.sqlite_path).startswith(str(tmp_path))
    assert str(cfg.manifest_path).startswith(str(tmp_path))


def test_telemetry_path_resolution_home_root(tmp_path: Path) -> None:
    from openminion.modules.telemetry.service import resolve_telemetry_db_path

    info = resolve_telemetry_db_path(home_root=tmp_path)
    assert info.path_mode == "integrated_runtime"
    assert info.path_source == "default_integrated"
    assert info.db_path.startswith(str(tmp_path))
    assert ".openminion/telemetry/telemetry.db" in info.db_path


def test_controlplane_config_defaults_to_home_root(tmp_path: Path) -> None:
    from openminion.modules.controlplane.config import load_config

    cfg = load_config(None, home_root=tmp_path)
    assert cfg.path_mode == "integrated_runtime"
    assert cfg.path_source == "default_integrated"
    assert str(cfg.sqlite_path).startswith(str(tmp_path))
    assert ".openminion/controlplane/cp.db" in str(cfg.sqlite_path)


def test_controlplane_telegram_poll_state_defaults_to_home_root(tmp_path: Path) -> None:
    from openminion.modules.controlplane.channels.telegram.config import load_config

    cfg = load_config(None, home_root=tmp_path).telegram
    assert cfg.polling.path_mode == "integrated_runtime"
    assert cfg.polling.path_source == "default_integrated"
    assert str(cfg.polling.state_sqlite_path).startswith(str(tmp_path))
    assert ".openminion/controlplane/telegram-poll-state.db" in str(
        cfg.polling.state_sqlite_path
    )


def test_debug_providers_emit_home_root_path_metadata(tmp_path: Path) -> None:
    from openminion.cli.commands.debug import (
        OpenMinionControlplaneDebugProvider,
        OpenMinionRegistryDebugProvider,
        OpenMinionTelemetryDebugProvider,
    )

    with patch.dict(
        os.environ,
        {
            "OPENMINION_HOME": str(tmp_path),
            "OPENMINION_DATA_ROOT": str(tmp_path / ".openminion"),
        },
        clear=False,
    ):
        registry_payload = OpenMinionRegistryDebugProvider()._probe()
        telemetry_payload = OpenMinionTelemetryDebugProvider()._probe()
        controlplane_payload = OpenMinionControlplaneDebugProvider()._probe()

    assert registry_payload.path_mode == "integrated_runtime"
    assert registry_payload.path_source in {"default_integrated", "explicit_override"}
    assert str(registry_payload.resolved_path).startswith(str(tmp_path))

    assert telemetry_payload.path_mode == "integrated_runtime"
    assert telemetry_payload.path_source == "default_integrated"
    assert str(telemetry_payload.resolved_path).startswith(str(tmp_path))

    assert controlplane_payload.path_mode == "integrated_runtime"
    assert controlplane_payload.path_source in {
        "default_integrated",
        "explicit_override",
    }
    assert str(controlplane_payload.resolved_path).startswith(str(tmp_path))


def test_telegram_debug_provider_emits_path_metadata(tmp_path: Path) -> None:
    from openminion.modules.controlplane.channels.telegram.debug_provider import (
        TelegramDebugProvider,
    )

    with patch.dict(
        os.environ,
        {
            "OPENMINION_HOME": str(tmp_path),
            "OPENMINION_DATA_ROOT": str(tmp_path / ".openminion"),
        },
        clear=False,
    ):
        payload = TelegramDebugProvider()._probe()

    assert payload.path_mode == "integrated_runtime"
    assert payload.path_source in {"default_integrated", "explicit_override"}
    assert str(payload.resolved_path).startswith(str(tmp_path))
