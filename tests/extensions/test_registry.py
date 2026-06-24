from __future__ import annotations

from pathlib import Path

import pytest

from openminion.services.agent.hooks import Hook
from openminion.services.runtime.discovery import DiscoveredHook
from openminion.services.runtime.contracts.manifest import (
    HookManifest,
    validate_plugin_manifest,
)
from openminion.services.agent.hooks import HookRegistry
from openminion.services.runtime.plugins import _build_custom_lookup


class _AlphaHook(Hook):
    name = "alpha"


class _BetaHook(Hook):
    name = "beta"


def test_plugin_registry_register_names_and_manifest_ids() -> None:
    registry = HookRegistry()
    registry.register(_AlphaHook(), manifest=_manifest("example.alpha"))
    registry.register(_BetaHook(), manifest=_manifest("example.beta"))

    assert registry.names() == ["alpha", "beta"]
    assert registry.manifest_ids() == ["example.alpha", "example.beta"]
    assert [item.id for item in registry.manifests()] == [
        "example.alpha",
        "example.beta",
    ]


def test_plugin_registry_rejects_duplicate_manifest_id() -> None:
    registry = HookRegistry()
    manifest = _manifest("example.alpha")
    registry.register(_AlphaHook(), manifest=manifest)

    with pytest.raises(RuntimeError, match="Duplicate hook manifest id"):
        registry.register(_BetaHook(), manifest=manifest)


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


def _manifest(plugin_id: str) -> HookManifest:
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


def _discovered(*, manifest_id: str, module_alias: str, stem: str) -> DiscoveredHook:
    root = Path("/tmp/extensions-registry-tests")
    return DiscoveredHook(
        manifest=_manifest(manifest_id),
        manifest_path=root / f"{stem}.manifest.json",
        module_path=root / f"{stem}.py",
        source_root=root,
        module_alias=module_alias,
    )
