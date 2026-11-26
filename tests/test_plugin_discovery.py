from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.services.runtime.plugins import (
    PLUGIN_MANIFEST_CONTRACT_VERSION,
    PluginManifestError,
    build_default_plugin_registry,
)
from openminion.services.runtime.plugins.discovery import (
    PluginDiscoveryError,
    discover_plugin_manifests,
    load_plugin_instance,
    load_plugin_module,
    plugin_bundle_digest,
)
from openminion.services.runtime.errors import PluginActivationError
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


def test_loaded_plugin_module_uses_sha256_path_digest(tmp_path: Path) -> None:
    custom_root = tmp_path / "plugins"
    _write_custom_plugin(custom_root, module_alias="hello", manifest_id="example.hello")
    discovered = discover_plugin_manifests([custom_root])[0]

    plugin = load_plugin_instance(discovered)

    digest = hashlib.sha256(str(discovered.module_path).encode("utf-8")).hexdigest()[
        :12
    ]
    assert plugin.__class__.__module__ == (
        f"openminion.extensions.dynamic.example_hello_{digest}"
    )


def test_plugin_bundle_digest_frames_alias_and_exact_bytes(tmp_path: Path) -> None:
    custom_root = tmp_path / "plugins"
    _write_custom_plugin(custom_root, module_alias="héllo", manifest_id="example.hello")
    discovered = discover_plugin_manifests([custom_root])[0]

    parts = (
        discovered.module_alias.encode("utf-8"),
        discovered.manifest_path.read_bytes(),
        discovered.module_path.read_bytes(),
    )
    expected = hashlib.sha256()
    for part in parts:
        expected.update(len(part).to_bytes(8, "big"))
        expected.update(part)
    first_digest = plugin_bundle_digest(discovered)
    assert first_digest == f"sha256:{expected.hexdigest()}"

    manifest_bytes = discovered.manifest_path.read_bytes()
    discovered.manifest_path.write_bytes(manifest_bytes + b"\n")
    assert plugin_bundle_digest(discovered) != first_digest
    discovered.manifest_path.write_bytes(manifest_bytes)

    discovered.module_path.write_text(
        discovered.module_path.read_text(encoding="utf-8") + "# café\n",
        encoding="utf-8",
    )
    assert plugin_bundle_digest(discovered) != first_digest

    renamed_root = tmp_path / "renamed"
    _write_custom_plugin(
        renamed_root,
        module_alias="other",
        manifest_id="example.hello",
    )
    renamed = discover_plugin_manifests([renamed_root])[0]
    renamed.manifest_path.write_bytes(discovered.manifest_path.read_bytes())
    renamed.module_path.write_bytes(discovered.module_path.read_bytes())
    assert plugin_bundle_digest(renamed) != plugin_bundle_digest(discovered)


def test_loads_custom_plugin_by_module_alias(tmp_path: Path) -> None:
    custom_root = tmp_path / "plugins"
    _write_custom_plugin(custom_root, module_alias="hello", manifest_id="example.hello")

    registry = _build_registry(custom_root, enabled_plugins=["hello"])

    assert registry.names() == ["hello"]
    assert registry.manifest_ids() == ["example.hello"]


@pytest.mark.parametrize("module_alias", [".", ".."])
def test_discovery_rejects_path_aliases(tmp_path: Path, module_alias: str) -> None:
    custom_root = tmp_path / "plugins"
    _write_custom_plugin(
        custom_root,
        module_alias=module_alias,
        manifest_id="example.unsafe",
    )

    with pytest.raises(RuntimeError, match="safe module alias"):
        discover_plugin_manifests([custom_root])


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


def test_verified_custom_plugin_requires_matching_module_checksum(
    tmp_path: Path,
) -> None:
    custom_root = tmp_path / "plugins"
    _write_custom_plugin(custom_root, module_alias="hello", manifest_id="example.hello")
    module_path = custom_root / "hello.py"
    manifest_path = custom_root / "hello.manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["provenance"] = {
        "source": "local-path",
        "checksum": f"sha256:{hashlib.sha256(module_path.read_bytes()).hexdigest()}",
        "verified": True,
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert discover_plugin_manifests([custom_root])[0].manifest.provenance_verified

    module_path.write_text(module_path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(PluginActivationError) as exc_info:
        discover_plugin_manifests([custom_root])
    assert exc_info.value.plugin_id == "example.hello"
    assert exc_info.value.stage == "checksum"
    assert exc_info.value.reason_code == "checksum_mismatch"


def test_verified_custom_plugin_rejects_malformed_checksum(tmp_path: Path) -> None:
    custom_root = tmp_path / "plugins"
    _write_custom_plugin(custom_root, module_alias="hello", manifest_id="example.hello")
    manifest_path = custom_root / "hello.manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["provenance"] = {
        "source": "local-path",
        "checksum": "sha256:not-a-digest",
        "verified": True,
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PluginActivationError) as exc_info:
        discover_plugin_manifests([custom_root])
    assert exc_info.value.plugin_id == "example.hello"
    assert exc_info.value.stage == "checksum"
    assert exc_info.value.reason_code == "checksum_malformed"


@pytest.mark.parametrize("explicit_version", [None, PLUGIN_MANIFEST_CONTRACT_VERSION])
def test_discovery_admits_omitted_and_supported_contract_version(
    tmp_path: Path, explicit_version: str | None
) -> None:
    custom_root = tmp_path / "plugins"
    marker = tmp_path / "module_loaded.txt"
    _write_custom_plugin(
        custom_root,
        module_alias="hello",
        manifest_id="example.hello",
        import_marker=marker,
    )
    if explicit_version is not None:
        manifest_path = custom_root / "hello.manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["contract_version"] = explicit_version
        manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    discovered = discover_plugin_manifests([custom_root])
    assert len(discovered) == 1
    assert discovered[0].manifest.contract_version == PLUGIN_MANIFEST_CONTRACT_VERSION
    assert not marker.exists()

    load_plugin_module(discovered[0])
    assert marker.exists()


@pytest.mark.parametrize(
    "invalid_version",
    ["2", "", "   ", 1, 2, None, False, True, {}, []],
)
def test_discovery_rejects_malformed_contract_version_before_loading(
    tmp_path: Path, invalid_version: object
) -> None:
    custom_root = tmp_path / "plugins"
    marker = tmp_path / "module_loaded.txt"
    _write_custom_plugin(
        custom_root,
        module_alias="hello",
        manifest_id="example.hello",
        import_marker=marker,
    )
    manifest_path = custom_root / "hello.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["contract_version"] = invalid_version
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    with pytest.raises(
        PluginDiscoveryError, match="contract_version must be '1'"
    ) as exc:
        discover_plugin_manifests([custom_root])
    assert isinstance(exc.value.__cause__, PluginManifestError)
    assert not marker.exists()


def _write_custom_plugin(
    root: Path,
    *,
    module_alias: str,
    manifest_id: str,
    class_name: str = "HelloPlugin",
    plugin_name: str | None = None,
    import_marker: Path | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    resolved_plugin_name = plugin_name or module_alias
    marker_block = (
        "from pathlib import Path\n"
        f"Path({str(import_marker)!r}).write_text('loaded', encoding='utf-8')\n"
        if import_marker is not None
        else ""
    )
    (root / f"{module_alias}.py").write_text(
        marker_block + "from openminion.services.runtime.plugins import Plugin\n\n"
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
