from __future__ import annotations

from .schemas import CapabilityPackManifest


class CapabilityPackRegistry:
    def __init__(self) -> None:
        self._packs: dict[str, CapabilityPackManifest] = {}

    def register(self, manifest: CapabilityPackManifest) -> None:
        existing = self._packs.get(manifest.pack_id)
        if existing is not None and existing.version != manifest.version:
            raise ValueError(
                f"capability pack {manifest.pack_id!r} is already registered at "
                f"version {existing.version!r}"
            )
        self._packs[manifest.pack_id] = manifest

    def get(self, pack_id: str) -> CapabilityPackManifest:
        try:
            return self._packs[pack_id]
        except KeyError as exc:
            raise KeyError(f"unknown capability pack: {pack_id}") from exc

    def list(self) -> tuple[CapabilityPackManifest, ...]:
        return tuple(self._packs[key] for key in sorted(self._packs))
