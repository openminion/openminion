from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.services.runtime.catalog import ExtensionCatalog
from openminion.services.runtime.plugins import build_default_plugin_registry


def test_system_runtime_plugin_allowlist_overrides_legacy_enabled_plugins(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugins"
    _write_plugin(plugin_root, module_alias="hello", manifest_id="example.hello")
    _write_plugin(plugin_root, module_alias="alpha", manifest_id="example.alpha")

    config = OpenMinionConfig.from_dict(
        {
            "enabled_plugins": ["example.alpha"],
            "system": {
                "runtime": {
                    "plugins": {
                        "enabled": ["example.hello"],
                    }
                }
            },
        }
    )

    with _plugin_paths_env([plugin_root]):
        registry = build_default_plugin_registry(config, logger=_NullLogger())

    assert registry.manifest_ids() == ["example.hello"]


def test_blocked_plugin_is_removed_from_effective_runtime_inventory(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugins"
    _write_plugin(plugin_root, module_alias="hello", manifest_id="example.hello")

    config = OpenMinionConfig.from_dict(
        {
            "enabled_plugins": ["example.hello"],
            "system": {
                "runtime": {
                    "plugins": {
                        "blocked": ["example.hello"],
                    }
                }
            },
        }
    )

    with _plugin_paths_env([plugin_root]):
        registry = build_default_plugin_registry(config, logger=_NullLogger())
        catalog = ExtensionCatalog.from_config(config, plugin_roots=[plugin_root])

    assert registry.manifest_ids() == []
    plugin_record = next(
        item for item in catalog.plugins if item.name == "example.hello"
    )
    assert plugin_record.enabled is False


def _write_plugin(root: Path, *, module_alias: str, manifest_id: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{module_alias}.py").write_text(
        "from openminion.services.runtime.plugins import Plugin\n\n"
        f"class {module_alias.title()}Plugin(Plugin):\n"
        f"    name = {module_alias!r}\n",
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
