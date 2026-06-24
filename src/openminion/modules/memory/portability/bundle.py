"""Small JSON memory-bundle helpers for public callers."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openminion.modules.memory.errors import InvalidArgumentError

from .codec import MEMORY_BUNDLE_VERSION


@dataclass
class MemoryBundle:
    """Portable memory items and caller metadata."""

    version: str = MEMORY_BUNDLE_VERSION
    items: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "items": self.items,
                "metadata": self.metadata,
            },
            ensure_ascii=True,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> "MemoryBundle":
        payload = json.loads(text)
        return cls(
            version=str(payload.get("version") or MEMORY_BUNDLE_VERSION),
            items=list(payload.get("items") or []),
            metadata=dict(payload.get("metadata") or {}),
        )


def save_bundle(bundle: MemoryBundle, path: str | Path) -> None:
    Path(path).write_text(bundle.to_json(), encoding="utf-8")


def load_bundle(path: str | Path) -> MemoryBundle:
    return MemoryBundle.from_json(Path(path).read_text(encoding="utf-8"))


def export_bundle(
    items: list[dict[str, Any]],
    *,
    metadata: dict[str, Any] | None = None,
) -> MemoryBundle:
    return MemoryBundle(items=list(items), metadata=dict(metadata or {}))


def import_bundle(
    bundle: MemoryBundle,
    *,
    trust_mode: str = "direct",
) -> dict[str, Any]:
    normalised = trust_mode.lower().strip()
    if normalised not in {"direct", "candidate"}:
        raise InvalidArgumentError(
            f"trust_mode must be 'direct' or 'candidate', got {trust_mode!r}"
        )
    return {
        "trust_mode": normalised,
        "bundle_version": bundle.version,
        "item_count": len(bundle.items),
        "metadata": dict(bundle.metadata),
    }


__all__ = [
    "MemoryBundle",
    "export_bundle",
    "import_bundle",
    "load_bundle",
    "save_bundle",
]
