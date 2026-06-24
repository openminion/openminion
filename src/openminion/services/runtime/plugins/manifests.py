import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from openminion.services.runtime.constants import (
    PLUGIN_PROVENANCE_SOURCES,
    PLUGIN_PROVENANCE_SOURCE_LOCAL_PATH,
    PLUGIN_PROVENANCE_SOURCE_VALUES,
    PLUGIN_TRUST_TIERS,
    PLUGIN_TRUST_TIER_LOCAL_DEV,
    PLUGIN_TRUST_TIER_VALUES,
)


class PluginManifestError(RuntimeError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("Invalid plugin manifest.")
        self.errors = list(errors)


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    version: str
    description: str
    config_schema: dict[str, Any]
    trust_tier: str
    requested_capabilities: tuple[str, ...]
    provenance_source: str
    provenance_uri: str
    provenance_publisher: str
    provenance_checksum: str
    provenance_verified: bool


def validate_plugin_manifest(payload: Mapping[str, Any]) -> PluginManifest:
    errors: list[str] = []

    plugin_id = _required_non_empty_string(payload.get("id"), "id", errors)
    name = _optional_string(payload.get("name"))
    if not name:
        name = plugin_id or "plugin"
    version = _optional_string(payload.get("version")) or "0.0.0"
    description = _optional_string(payload.get("description"))

    config_schema = payload.get("config_schema")
    if not isinstance(config_schema, dict):
        errors.append("config_schema must be an object.")
        config_schema = {}
    elif str(config_schema.get("type", "")).strip().lower() != "object":
        errors.append("config_schema.type must be 'object'.")

    trust_tier = (
        _optional_string(payload.get("trust_tier")) or PLUGIN_TRUST_TIER_LOCAL_DEV
    )
    if trust_tier not in PLUGIN_TRUST_TIERS:
        errors.append(
            f"trust_tier must be one of: {'|'.join(PLUGIN_TRUST_TIER_VALUES)}."
        )

    requested_capabilities_raw = payload.get("requested_capabilities", [])
    if not isinstance(requested_capabilities_raw, list):
        errors.append("requested_capabilities must be an array of strings.")
        requested_capabilities_raw = []
    requested_capabilities: list[str] = []
    for capability in requested_capabilities_raw:
        if not isinstance(capability, str):
            errors.append("requested_capabilities must contain only strings.")
            continue
        normalized = capability.strip()
        if normalized:
            requested_capabilities.append(normalized)

    provenance_payload = payload.get("provenance")
    if provenance_payload is None:
        provenance_payload = {}
    if not isinstance(provenance_payload, dict):
        errors.append("provenance must be an object when provided.")
        provenance_payload = {}

    provenance_source = (
        _optional_string(provenance_payload.get("source"))
        or PLUGIN_PROVENANCE_SOURCE_LOCAL_PATH
    )
    if provenance_source not in PLUGIN_PROVENANCE_SOURCES:
        errors.append(
            "provenance.source must be one of: "
            f"{'|'.join(PLUGIN_PROVENANCE_SOURCE_VALUES)}."
        )
    provenance_uri = _optional_string(provenance_payload.get("uri"))
    provenance_publisher = _optional_string(provenance_payload.get("publisher"))
    provenance_checksum = _optional_string(provenance_payload.get("checksum"))
    provenance_verified = _optional_bool(
        provenance_payload.get("verified"), default=False
    )

    if errors:
        raise PluginManifestError(errors)

    return PluginManifest(
        id=plugin_id,
        name=name,
        version=version,
        description=description,
        config_schema=dict(config_schema),
        trust_tier=trust_tier,
        requested_capabilities=tuple(sorted(set(requested_capabilities))),
        provenance_source=provenance_source,
        provenance_uri=provenance_uri,
        provenance_publisher=provenance_publisher,
        provenance_checksum=provenance_checksum,
        provenance_verified=provenance_verified,
    )


def load_plugin_manifest(path: str | Path) -> PluginManifest:
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PluginManifestError([f"manifest not found: {manifest_path}"]) from exc
    except json.JSONDecodeError as exc:
        raise PluginManifestError([f"manifest is not valid JSON: {exc}"]) from exc
    if not isinstance(payload, dict):
        raise PluginManifestError(["manifest root must be an object."])
    return validate_plugin_manifest(payload)


def _required_non_empty_string(raw: Any, field: str, errors: list[str]) -> str:
    if not isinstance(raw, str) or not raw.strip():
        errors.append(f"{field} must be a non-empty string.")
        return ""
    return raw.strip()


def _optional_string(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def _optional_bool(raw: Any, *, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(raw, (int, float)):
        return bool(raw)
    return default
