from __future__ import annotations

from pathlib import Path

import pytest

from openminion.services.runtime.plugins import Plugin, PluginRegistry
from openminion.services.runtime.plugins.discovery import DiscoveredPlugin
from openminion.services.runtime.plugins.manifests import (
    PluginManifest,
    validate_plugin_manifest,
)
from openminion.services.runtime.plugins import _build_custom_lookup


class _AlphaPlugin(Plugin):
    name = "alpha"


class _BetaPlugin(Plugin):
    name = "beta"


def test_plugin_registry_register_names_and_manifest_ids() -> None:
    registry = PluginRegistry()
    registry.register(_AlphaPlugin(), manifest=_manifest("example.alpha"))
    registry.register(_BetaPlugin(), manifest=_manifest("example.beta"))

    assert registry.names() == ["alpha", "beta"]
    assert registry.manifest_ids() == ["example.alpha", "example.beta"]
    assert [item.id for item in registry.manifests()] == [
        "example.alpha",
        "example.beta",
    ]


def test_plugin_registry_rejects_duplicate_manifest_id() -> None:
    registry = PluginRegistry()
    manifest = _manifest("example.alpha")
    registry.register(_AlphaPlugin(), manifest=manifest)

    with pytest.raises(RuntimeError, match="Duplicate plugin manifest id"):
        registry.register(_BetaPlugin(), manifest=manifest)


def test_build_custom_lookup_detects_conflicting_alias() -> None:
    discovered = [
        _discovered(manifest_id="example.alpha", module_alias="shared", stem="alpha"),
        _discovered(manifest_id="example.beta", module_alias="shared", stem="beta"),
    ]
    with pytest.raises(RuntimeError, match="Plugin discovery conflict for key shared"):
        _build_custom_lookup(discovered_plugins=discovered, reserved_lookup_keys=set())


def test_build_custom_lookup_rejects_reserved_manifest_id() -> None:
    discovered = [
        _discovered(
            manifest_id="builtin.validate", module_alias="custom", stem="custom"
        ),
    ]
    with pytest.raises(RuntimeError, match="reserved plugin id/key"):
        _build_custom_lookup(
            discovered_plugins=discovered,
            reserved_lookup_keys={"validate", "builtin.validate"},
        )


def test_build_custom_lookup_keeps_manifest_id_when_alias_is_reserved() -> None:
    discovered = [
        _discovered(
            manifest_id="example.custom", module_alias="validate", stem="custom"
        ),
    ]
    lookup = _build_custom_lookup(
        discovered_plugins=discovered,
        reserved_lookup_keys={"validate", "builtin.validate"},
    )
    assert "validate" not in lookup
    assert "example.custom" in lookup
    assert lookup["example.custom"].module_alias == "validate"


def _manifest(plugin_id: str) -> PluginManifest:
    return validate_plugin_manifest(
        {
            "id": plugin_id,
            "name": plugin_id,
            "version": "0.0.1",
            "description": "test plugin",
            "config_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "trust_tier": "local-dev",
            "requested_capabilities": [],
            "provenance": {
                "source": "local-path",
                "publisher": "test",
                "checksum": "x",
                "verified": False,
            },
        }
    )


def _discovered(*, manifest_id: str, module_alias: str, stem: str) -> DiscoveredPlugin:
    root = Path("/tmp/extensions-registry-tests")
    return DiscoveredPlugin(
        manifest=_manifest(manifest_id),
        manifest_path=root / f"{stem}.manifest.json",
        module_path=root / f"{stem}.py",
        source_root=root,
        module_alias=module_alias,
    )
