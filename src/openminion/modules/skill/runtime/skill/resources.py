from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Any

from openminion.modules.skill.constants import (
    SKILL_BUNDLE_MAX_RESOURCES,
    SKILL_BUNDLE_MAX_RESOURCE_BYTES,
    SKILL_BUNDLE_MAX_TOTAL_RESOURCE_BYTES,
)


def collect_bundle_resources(
    bundle_root: Path | None, *, blob_store: Any
) -> tuple[list[dict[str, Any]], list[str]]:
    if bundle_root is None:
        return [], []
    root = bundle_root.resolve()
    resources: list[dict[str, Any]] = []
    warnings: list[str] = []
    total_bytes = 0
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        if current != root and "SKILL.md" in filenames:
            dirnames.clear()
            continue
        visible_dirs = []
        for name in sorted(dirnames):
            path = current / name
            if name.startswith("."):
                continue
            if path.is_symlink():
                warnings.append("bundle.resources.symlink_skipped")
            else:
                visible_dirs.append(name)
        dirnames[:] = visible_dirs
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = current / name
            relative = path.relative_to(root)
            relative_path = relative.as_posix()
            if relative_path in {"SKILL.md", "agents/openai.yaml"}:
                continue
            if path.is_symlink():
                warnings.append("bundle.resources.symlink_skipped")
                continue
            if not path.is_file():
                warnings.append("bundle.resources.non_regular_skipped")
                continue
            if len(resources) >= SKILL_BUNDLE_MAX_RESOURCES:
                warnings.append("bundle.resources.count_limit")
                return resources, warnings
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                warnings.append("bundle.resources.path_escape_skipped")
                continue
            size = resolved.stat().st_size
            if size > SKILL_BUNDLE_MAX_RESOURCE_BYTES:
                warnings.append("bundle.resources.file_size_limit")
                continue
            if total_bytes + size > SKILL_BUNDLE_MAX_TOTAL_RESOURCE_BYTES:
                warnings.append("bundle.resources.total_size_limit")
                return resources, warnings
            payload = resolved.read_bytes()
            media_type = (
                mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
            )
            root_kind = relative.parts[0]
            kind = (
                root_kind
                if root_kind in {"references", "assets", "scripts"}
                else "supporting"
            )
            ref = blob_store.put_bytes(
                payload,
                media_type=media_type,
                ext=resolved.suffix.lstrip("."),
                meta={"skill_resource_path": relative_path},
            )
            resources.append(
                {
                    "path": relative_path,
                    "kind": kind,
                    "size_bytes": size,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "artifact_ref": f"artifact://sha256/{ref.hash}",
                    "executable": False,
                }
            )
            total_bytes += size
    return resources, warnings
