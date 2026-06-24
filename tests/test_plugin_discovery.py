from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.services.runtime.plugins import build_default_plugin_registry
from tests._csc_fixtures import _csc_install_default_agent


def _config(enabled_plugins: list[str]) -> OpenMinionConfig:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.enabled_plugins = enabled_plugins
    return config


def _build_registry(custom_root: Path, *, enabled_plugins: list[str]):
    with _plugin_paths_env([custom_root]):
        return build_default_plugin_registry(
            _config(enabled_plugins),
            logger=_NullLogger(),
        )


def test_loads_custom_plugin_by_manifest_id(tmp_path: Path) -> None:
    custom_root = tmp_path / "plugins"
    _write_custom_plugin(custom_root, module_alias="hello", manifest_id="example.hello")

    registry = _build_registry(custom_root, enabled_plugins=["example.hello"])

    assert registry.names() == ["hello"]
    assert registry.manifest_ids() == ["example.hello"]


def test_loads_custom_plugin_by_module_alias(tmp_path: Path) -> None:
    custom_root = tmp_path / "plugins"
    _write_custom_plugin(custom_root, module_alias="hello", manifest_id="example.hello")

    registry = _build_registry(custom_root, enabled_plugins=["hello"])

    assert registry.names() == ["hello"]
    assert registry.manifest_ids() == ["example.hello"]


def test_builtin_alias_takes_precedence_over_custom_alias(tmp_path: Path) -> None:
    custom_root = tmp_path / "plugins"
    _write_custom_plugin(
        custom_root,
        module_alias="validate",
        manifest_id="example.validate",
        class_name="CustomValidatePlugin",
        plugin_name="custom-validate",
    )

    registry = _build_registry(custom_root, enabled_plugins=["validate"])

    assert registry.names() == ["validate"]
    assert registry.manifest_ids() == ["builtin.validate"]


def test_duplicate_discovery_key_conflict_raises(tmp_path: Path) -> None:
    custom_root = tmp_path / "plugins"
    _write_custom_plugin(custom_root, module_alias="alpha", manifest_id="example.alpha")
    _write_custom_plugin(custom_root, module_alias="beta", manifest_id="example.alpha")

    with pytest.raises(RuntimeError, match="conflict"):
        _build_registry(custom_root, enabled_plugins=["example.alpha"])


def test_unknown_enabled_plugin_raises(tmp_path: Path) -> None:
    custom_root = tmp_path / "plugins"
    custom_root.mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match="not found"):
        _build_registry(custom_root, enabled_plugins=["missing.plugin"])


def test_invalid_discovered_manifest_fails_fast(tmp_path: Path) -> None:
    custom_root = tmp_path / "plugins"
    custom_root.mkdir(parents=True, exist_ok=True)
    (custom_root / "broken.py").write_text(
        "from openminion.services.runtime.plugins import Plugin\n\nclass BrokenPlugin(Plugin):\n    name='broken'\n",
        encoding="utf-8",
    )
    (custom_root / "broken.manifest.json").write_text(
        json.dumps({"id": "example.broken"}, indent=2),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Invalid plugin manifest"):
        _build_registry(custom_root, enabled_plugins=["validate"])


def _write_custom_plugin(
    root: Path,
    *,
    module_alias: str,
    manifest_id: str,
    class_name: str = "HelloPlugin",
    plugin_name: str | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    resolved_plugin_name = plugin_name or module_alias
    (root / f"{module_alias}.py").write_text(
        "from openminion.services.runtime.plugins import Plugin\n\n"
        f"class {class_name}(Plugin):\n"
        f"    name = {resolved_plugin_name!r}\n",
        encoding="utf-8",
    )
    (root / f"{module_alias}.manifest.json").write_text(
        json.dumps(
            {
                "id": manifest_id,
                "name": manifest_id,
                "version": "0.0.1",
                "description": "test plugin",
                "config_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "trust_tier": "local-dev",
                "requested_capabilities": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


@contextmanager
def _plugin_paths_env(paths: list[Path]):
    previous = os.environ.get("OPENMINION_PLUGIN_PATHS")
    os.environ["OPENMINION_PLUGIN_PATHS"] = os.pathsep.join(str(path) for path in paths)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("OPENMINION_PLUGIN_PATHS", None)
        else:
            os.environ["OPENMINION_PLUGIN_PATHS"] = previous


class _NullLogger:
    def debug(self, _msg: str, *_args) -> None:
        return
