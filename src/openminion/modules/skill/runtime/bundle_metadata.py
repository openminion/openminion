from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from openminion.modules.skill.models import normalize_text_list

try:
    import yaml  # type: ignore[import-untyped]
except ModuleNotFoundError:  # pragma: no cover
    yaml = None

BUNDLE_METADATA_SOURCE_NOT_ATTEMPTED = "not_attempted"
BUNDLE_METADATA_SOURCE_NONE = "none"
BUNDLE_METADATA_SOURCE_OPENAI = "openai"
BUNDLE_METADATA_TRUST_TRUSTED_LOCAL = "trusted_local"
BUNDLE_METADATA_TRUST_TRUSTED_REMOTE = "trusted_remote"
BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL = "untrusted_local"
BUNDLE_METADATA_TRUST_UNTRUSTED_REMOTE = "untrusted_remote"

_BUNDLE_METADATA_SOURCE_VALUES: frozenset[str] = frozenset(
    {
        BUNDLE_METADATA_SOURCE_NOT_ATTEMPTED,
        BUNDLE_METADATA_SOURCE_NONE,
        BUNDLE_METADATA_SOURCE_OPENAI,
    }
)
_BUNDLE_METADATA_TRUST_VALUES: frozenset[str] = frozenset(
    {
        BUNDLE_METADATA_TRUST_TRUSTED_LOCAL,
        BUNDLE_METADATA_TRUST_TRUSTED_REMOTE,
        BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL,
        BUNDLE_METADATA_TRUST_UNTRUSTED_REMOTE,
    }
)

_OPENAI_COMPANION_PATH = Path("agents/openai.yaml")


def agent_skills_metadata(
    front_matter: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    metadata: dict[str, Any] = {}
    warnings: list[str] = []
    for key in ("license", "compatibility"):
        value = front_matter.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            warnings.append(f"parse.warning:agent_skills_invalid_type:{key}")
            continue
        text = value.strip()
        if text:
            metadata[key] = text
    raw_metadata = front_matter.get("metadata")
    if raw_metadata is not None:
        if isinstance(raw_metadata, Mapping):
            metadata["metadata"] = {
                str(key): value for key, value in raw_metadata.items()
            }
        else:
            warnings.append("parse.warning:agent_skills_invalid_type:metadata")
    allowed_tools = front_matter.get("allowed-tools")
    if allowed_tools is not None:
        if isinstance(allowed_tools, str):
            metadata["allowed_tools"] = allowed_tools.split()
        elif isinstance(allowed_tools, list):
            metadata["allowed_tools"] = normalize_text_list(allowed_tools)
        else:
            warnings.append("parse.warning:agent_skills_invalid_type:allowed-tools")
    return metadata, warnings


def agent_skills_conformance_warnings(
    *,
    name: str,
    description: str,
    metadata: Mapping[str, Any],
    resources: list[dict[str, Any]],
) -> list[str]:
    if not metadata and not resources:
        return []
    warnings: list[str] = []
    if len(name) > 64 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        warnings.append("parse.warning:agent_skills_name_nonconforming")
    if len(description) > 1024:
        warnings.append("parse.warning:agent_skills_description_too_long")
    if len(str(metadata.get("compatibility", ""))) > 500:
        warnings.append("parse.warning:agent_skills_compatibility_too_long")
    return warnings


def validate_bundle_metadata_trust(trust: str) -> str:
    normalized = str(trust or "").strip().lower()
    if normalized not in _BUNDLE_METADATA_TRUST_VALUES:
        raise ValueError(
            f"bundle_metadata.trust must be one of "
            f"{sorted(_BUNDLE_METADATA_TRUST_VALUES)}, got {trust!r}"
        )
    return normalized


def resolve_bundle_metadata_trust(
    trust: str | None,
    *,
    remote: bool,
) -> str:
    if trust is not None:
        return validate_bundle_metadata_trust(trust)
    return (
        BUNDLE_METADATA_TRUST_UNTRUSTED_REMOTE
        if remote
        else BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL
    )


def _empty_companion_metadata(source: str, *, trust: str) -> dict[str, Any]:
    if source not in _BUNDLE_METADATA_SOURCE_VALUES:
        raise ValueError(
            f"bundle_metadata.source must be one of "
            f"{sorted(_BUNDLE_METADATA_SOURCE_VALUES)}, got {source!r}"
        )
    normalized_trust = validate_bundle_metadata_trust(trust)
    return {
        "display_name": None,
        "short_description": None,
        "default_prompt": None,
        "dependency_hints": {},
        "bundle_metadata": {"source": source, "trust": normalized_trust},
    }


def load_companion_metadata(
    bundle_root: Path | None,
    *,
    trust: str | None = None,
) -> dict[str, Any]:
    """Load companion metadata and carry an explicit source enum."""
    normalized_trust = resolve_bundle_metadata_trust(trust, remote=False)
    if bundle_root is None:
        return _empty_companion_metadata(
            BUNDLE_METADATA_SOURCE_NOT_ATTEMPTED,
            trust=normalized_trust,
        )

    companion_path = Path(bundle_root) / _OPENAI_COMPANION_PATH
    if not companion_path.exists():
        return _empty_companion_metadata(
            BUNDLE_METADATA_SOURCE_NONE,
            trust=normalized_trust,
        )

    payload = _load_yaml_mapping(companion_path)
    interface_raw = payload.get("interface")
    interface: dict[str, Any] = interface_raw if isinstance(interface_raw, dict) else {}
    dependencies_raw = payload.get("dependencies")
    dependencies: dict[str, Any] = (
        dependencies_raw if isinstance(dependencies_raw, dict) else {}
    )
    return {
        "display_name": _as_text(interface.get("display_name")),
        "short_description": _as_text(interface.get("short_description")),
        "default_prompt": _as_text(interface.get("default_prompt")),
        "dependency_hints": dependencies,
        "bundle_metadata": {
            "source": BUNDLE_METADATA_SOURCE_OPENAI,
            "trust": normalized_trust,
            "path": str(_OPENAI_COMPANION_PATH),
            "payload": payload,
        },
    }


def companion_metadata_unavailable_warning(
    companion_metadata: dict[str, Any],
) -> str | None:
    bundle_block = companion_metadata.get("bundle_metadata") or {}
    if (
        isinstance(bundle_block, dict)
        and str(bundle_block.get("source", "")).strip() == BUNDLE_METADATA_SOURCE_NONE
    ):
        return "parse.warning:companion_metadata_unavailable"
    return None


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if yaml is None:
        return {}
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return parsed if isinstance(parsed, dict) else {}


def _as_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
