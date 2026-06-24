import json

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.services.runtime.plugins import (
    build_default_plugin_registry,
    load_plugin_manifest,
    validate_plugin_manifest,
)
from tests._csc_fixtures import _csc_install_default_agent


def test_validate_plugin_manifest_accepts_valid_payload() -> None:
    manifest = validate_plugin_manifest(
        {
            "id": "example.sanitizer",
            "name": "Sanitizer Plugin",
            "version": "0.0.1",
            "description": "Sample manifest",
            "config_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "trust_tier": "local-dev",
            "requested_capabilities": [
                "message.inbound.read",
                "message.outbound.modify",
            ],
        }
    )
    assert manifest.id == "example.sanitizer"
    assert manifest.config_schema["type"] == "object"
    assert manifest.requested_capabilities == (
        "message.inbound.read",
        "message.outbound.modify",
    )
    assert manifest.provenance_source == "local-path"
    assert not manifest.provenance_verified


def test_validate_plugin_manifest_accepts_minimal_required_fields() -> None:
    manifest = validate_plugin_manifest(
        {
            "id": "example.minimal",
            "config_schema": {"type": "object"},
        }
    )
    assert manifest.id == "example.minimal"
    assert manifest.name == "example.minimal"
    assert manifest.version == "0.0.0"
    assert manifest.provenance_source == "local-path"
    assert manifest.provenance_publisher == ""


def test_validate_plugin_manifest_accepts_provenance_payload() -> None:
    manifest = validate_plugin_manifest(
        {
            "id": "example.provenance",
            "config_schema": {"type": "object"},
            "trust_tier": "verified",
            "requested_capabilities": ["message.inbound.read"],
            "provenance": {
                "source": "registry",
                "uri": "https://example.test/plugins/example.provenance",
                "publisher": "example-inc",
                "checksum": "sha256:abc123",
                "verified": True,
            },
        }
    )
    assert manifest.provenance_source == "registry"
    assert manifest.provenance_verified
    assert manifest.provenance_publisher == "example-inc"
    assert manifest.provenance_checksum == "sha256:abc123"


def test_validate_plugin_manifest_requires_id_and_config_schema() -> None:
    with pytest.raises(Exception) as exc_info:
        validate_plugin_manifest(
            {
                "name": "Invalid",
                "version": "0.0.1",
                "config_schema": {"type": "array"},
            }
        )
    errors = exc_info.value.errors
    assert "id must be a non-empty string." in errors
    assert "config_schema.type must be 'object'." in errors


def test_validate_plugin_manifest_rejects_invalid_provenance_source() -> None:
    with pytest.raises(Exception) as exc_info:
        validate_plugin_manifest(
            {
                "id": "example.invalid-provenance",
                "config_schema": {"type": "object"},
                "provenance": {"source": "unknown-source"},
            }
        )
    errors = exc_info.value.errors
    assert (
        "provenance.source must be one of: builtin|local-path|git|registry|package."
        in errors
    )


def test_load_plugin_manifest_reads_json_file(tmp_path) -> None:
    path = tmp_path / "plugin.manifest.json"
    path.write_text(
        json.dumps(
            {
                "id": "example.loader",
                "name": "Loader Plugin",
                "version": "1.2.3",
                "config_schema": {"type": "object", "properties": {}},
            }
        ),
        encoding="utf-8",
    )
    manifest = load_plugin_manifest(path)
    assert manifest.id == "example.loader"
    assert manifest.version == "1.2.3"


def test_default_plugin_registry_tracks_manifest_ids() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.enabled_plugins = ["validate"]
    registry = build_default_plugin_registry(
        config, logger=type("_NullLogger", (), {"debug": lambda *_args: None})()
    )
    assert registry.manifest_ids() == ["builtin.validate"]
