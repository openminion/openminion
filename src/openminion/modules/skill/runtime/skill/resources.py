"""Capture bundle resources and export retained content without executing it."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

from openminion.modules.skill.constants import (
    SKILL_BUNDLE_MAX_RESOURCES,
    SKILL_BUNDLE_MAX_RESOURCE_BYTES,
    SKILL_BUNDLE_MAX_TOTAL_RESOURCE_BYTES,
)
from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.models import SkillPackage
from openminion.modules.storage.backends.blob_store import BlobStore


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


def write_bundle_export(
    package: SkillPackage, *, blob_store: BlobStore, destination: str | Path
) -> dict[str, Any]:
    target = Path(destination).expanduser().absolute()
    if (
        "\x00" in str(target)
        or ".." in target.parts
        or any(path.is_symlink() for path in (target, *target.parents))
        or target.exists()
        or not target.parent.is_dir()
    ):
        raise SkillError(
            "INVALID_ARGUMENT",
            "Destination must be new with an existing, symlink-free parent.",
        )
    files, warnings = _export_files(package, blob_store)
    _validate_export_paths([name for name, _ in files])
    _write_export_files(target, files)
    return {
        "destination": str(target),
        "paths": [name for name, _ in files],
        "warnings": warnings,
    }


def _export_files(
    package: SkillPackage, blob_store: BlobStore
) -> tuple[list[tuple[str, bytes]], list[str]]:
    prefix = "artifact://sha256/"
    if not package.source_artifact_ref.startswith(prefix):
        raise SkillError(
            "SKILL_EXPORT_SOURCE_UNAVAILABLE",
            "Export requires retained Markdown bytes.",
        )
    files = [
        (
            "SKILL.md",
            _read_export_blob(blob_store, package.source_artifact_ref[len(prefix) :]),
        )
    ]
    warnings = []
    payload = package.bundle_metadata.get("payload")
    if payload is not None:
        if not isinstance(payload, dict):
            raise SkillError(
                "SKILL_EXPORT_INTEGRITY_ERROR", "Invalid companion mapping."
            )
        files.append(("agents/openai.yaml", yaml.safe_dump(payload).encode("utf-8")))
        warnings.append("export.openai_yaml_semantic_only")
    if len(package.resources) > SKILL_BUNDLE_MAX_RESOURCES:
        raise SkillError(
            "SKILL_EXPORT_INTEGRITY_ERROR", "Resource count exceeds limit."
        )
    total_bytes = 0
    for resource in package.resources:
        name = resource["path"]
        if name.casefold() in {"skill.md", "agents/openai.yaml"}:
            raise SkillError(
                "INVALID_ARGUMENT", "Resource collides with a reserved output."
            )
        content = _read_export_blob(
            blob_store, resource["sha256"], max_bytes=SKILL_BUNDLE_MAX_RESOURCE_BYTES
        )
        total_bytes += len(content)
        if (
            len(content) != resource["size_bytes"]
            or len(content) > SKILL_BUNDLE_MAX_RESOURCE_BYTES
            or total_bytes > SKILL_BUNDLE_MAX_TOTAL_RESOURCE_BYTES
        ):
            raise SkillError(
                "SKILL_EXPORT_INTEGRITY_ERROR", "Resource size exceeds its contract."
            )
        files.append((name, content))
    return files, warnings


def _read_export_blob(
    blob_store: BlobStore, digest: str, *, max_bytes: int | None = None
) -> bytes:
    try:
        with blob_store.open(digest) as handle:
            content: bytes = handle.read(-1 if max_bytes is None else max_bytes + 1)
    except OSError as exc:
        raise SkillError(
            "SKILL_EXPORT_SOURCE_UNAVAILABLE", "Retained bundle bytes are unavailable."
        ) from exc
    except ValueError as exc:
        raise SkillError(
            "SKILL_EXPORT_INTEGRITY_ERROR", "Invalid retained blob digest."
        ) from exc
    if hashlib.sha256(content).hexdigest() != digest:
        raise SkillError(
            "SKILL_EXPORT_INTEGRITY_ERROR", "Retained blob digest mismatch."
        )
    return content


def _validate_export_paths(names: list[str]) -> None:
    entries: dict[str, tuple[str, bool]] = {}
    for name in names:
        parts = name.split("/")
        if (
            PurePosixPath(name).is_absolute()
            or PureWindowsPath(name).drive
            or "\\" in name
            or "\x00" in name
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise SkillError("INVALID_ARGUMENT", "Unsafe bundle output path.")
        for index in range(1, len(parts) + 1):
            path = "/".join(parts[:index])
            key = unicodedata.normalize("NFD", path).casefold()
            is_file = index == len(parts)
            existing = entries.get(key)
            if existing is not None and (existing != (path, False) or is_file):
                raise SkillError("INVALID_ARGUMENT", "Colliding bundle output paths.")
            entries[key] = (path, is_file)


def _write_export_files(target: Path, files: list[tuple[str, bytes]]) -> None:
    try:
        target.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise SkillError(
            "INVALID_ARGUMENT", "Export destination already exists."
        ) from exc
    except OSError as exc:
        raise SkillError(
            "SKILL_EXPORT_WRITE_FAILED", "Cannot create export destination."
        ) from exc
    complete = False
    try:
        for name, content in files:
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as handle:
                handle.write(content)
        complete = True
    except OSError as exc:
        raise SkillError(
            "SKILL_EXPORT_WRITE_FAILED", "Cannot write bundle export."
        ) from exc
    finally:
        if not complete:
            try:
                shutil.rmtree(target)
            except OSError as exc:
                raise SkillError(
                    "SKILL_EXPORT_WRITE_FAILED",
                    "Export failed and its partial directory could not be removed.",
                    {"destination": str(target)},
                ) from exc
