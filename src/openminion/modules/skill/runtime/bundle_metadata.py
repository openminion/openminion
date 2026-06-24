from pathlib import Path
from typing import Any

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
    if remote:
        return BUNDLE_METADATA_TRUST_UNTRUSTED_REMOTE
    return BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL


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
    normalized_trust = resolve_bundle_metadata_trust(
        trust,
        remote=False,
    )
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
    if not isinstance(bundle_block, dict):
        return None
    source = str(bundle_block.get("source", "")).strip()
    if source == BUNDLE_METADATA_SOURCE_NONE:
        return "parse.warning:companion_metadata_unavailable"
    return None


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if yaml is None:
        return {}
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _as_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
