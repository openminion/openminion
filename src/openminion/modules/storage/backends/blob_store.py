from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, BinaryIO

from openminion.base.time import utc_now_iso as iso_now
from openminion.base.version import OPENMINION_VERSION
from openminion.modules.storage.interfaces import (
    STORAGE_INTERFACE_VERSION,
    BackendDescriptor,
)
from openminion.modules.storage.models import BlobRef
from openminion.modules.storage.io import atomic_write_text


class BlobStore(ABC):
    contract_version = STORAGE_INTERFACE_VERSION

    @abstractmethod
    def put_bytes(
        self,
        data: bytes,
        media_type: str = "application/octet-stream",
        ext: str = "",
        meta: dict[str, Any] | None = None,
    ) -> BlobRef:
        raise NotImplementedError

    @abstractmethod
    def put_file(
        self,
        path: str | Path,
        media_type: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> BlobRef:
        raise NotImplementedError

    @abstractmethod
    def open(self, ref: BlobRef | str) -> BinaryIO:
        raise NotImplementedError

    @abstractmethod
    def stat(self, ref: BlobRef | str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def gc(self, policy: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def verify(self, digest: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def healthcheck(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def describe_backend(self) -> BackendDescriptor:
        raise NotImplementedError


class BlobStoreFS(BlobStore):
    """Filesystem CAS store under artifacts/sha256/<prefix>/<hash>.<ext>."""

    contract_version = STORAGE_INTERFACE_VERSION

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve(strict=False)
        self.blob_root = self.root_dir / "artifacts" / "sha256"
        self.blob_root.mkdir(parents=True, exist_ok=True)

    def put_bytes(
        self,
        data: bytes,
        media_type: str = "application/octet-stream",
        ext: str = "",
        meta: dict[str, Any] | None = None,
    ) -> BlobRef:
        digest = hashlib.sha256(data).hexdigest()
        return self.put_known_hash_bytes(
            digest,
            data,
            media_type=media_type,
            ext=ext,
            meta=meta,
        )

    def put_known_hash_bytes(
        self,
        digest: str,
        data: bytes,
        *,
        media_type: str = "application/octet-stream",
        ext: str = "",
        meta: dict[str, Any] | None = None,
    ) -> BlobRef:
        hash_value = _normalize_hash(digest)
        existing = self._existing_ref(hash_value)
        if existing is not None:
            return existing

        ext_value = _normalize_ext(ext)
        target = self._target_path(hash_value, ext=ext_value)
        created_at = iso_now()

        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.parent / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            with tmp.open("wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
            _fsync_directory(target.parent)

        payload_meta = {
            "algo": "sha256",
            "hash": hash_value,
            "media_type": media_type,
            "ext": ext_value or None,
            "size": int(target.stat().st_size),
            "created_at": created_at,
            "meta": meta or {},
        }
        self._write_meta(hash_value, payload_meta)

        return BlobRef(
            algo="sha256",
            hash=hash_value,
            path=str(target),
            size=int(target.stat().st_size),
            media_type=media_type,
            created_at=created_at,
            ext=ext_value or None,
        )

    def put_file(
        self,
        path: str | Path,
        media_type: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> BlobRef:
        src = Path(path).expanduser().resolve(strict=True)
        digest = _hash_file(src)
        ext_value = _normalize_ext(src.suffix.lstrip("."))
        mime = (
            media_type
            or mimetypes.guess_type(src.name)[0]
            or "application/octet-stream"
        )
        return self.put_known_hash_file(
            digest,
            src,
            media_type=mime,
            ext=ext_value,
            meta=meta,
        )

    def put_known_hash_file(
        self,
        digest: str,
        path: str | Path,
        *,
        media_type: str = "application/octet-stream",
        ext: str = "",
        meta: dict[str, Any] | None = None,
    ) -> BlobRef:
        src = Path(path).expanduser().resolve(strict=True)
        hash_value = _normalize_hash(digest)
        existing = self._existing_ref(hash_value)
        if existing is not None:
            return existing

        ext_value = _normalize_ext(ext)
        target = self._target_path(hash_value, ext=ext_value)
        created_at = iso_now()

        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.parent / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            with src.open("rb") as in_fh, tmp.open("wb") as out_fh:
                shutil.copyfileobj(in_fh, out_fh)
                out_fh.flush()
                os.fsync(out_fh.fileno())
            os.replace(tmp, target)
            _fsync_directory(target.parent)

        payload_meta = {
            "algo": "sha256",
            "hash": hash_value,
            "media_type": media_type,
            "ext": ext_value or None,
            "size": int(target.stat().st_size),
            "created_at": created_at,
            "meta": meta or {},
        }
        self._write_meta(hash_value, payload_meta)

        return BlobRef(
            algo="sha256",
            hash=hash_value,
            path=str(target),
            size=int(target.stat().st_size),
            media_type=media_type,
            created_at=created_at,
            ext=ext_value or None,
        )

    def open(self, ref: BlobRef | str) -> BinaryIO:
        return self._resolve_path(ref).open("rb")

    def stat(self, ref: BlobRef | str) -> dict[str, Any]:
        path = self._resolve_path(ref)
        hash_value = _ref_to_hash(ref)
        sidecar = self._meta_path(hash_value)
        meta: dict[str, Any] = {}
        if sidecar.exists():
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = {}
        return {
            "size": int(path.stat().st_size),
            "hash": hash_value,
            "media_type": str(meta.get("media_type", "application/octet-stream")),
            "created_at": str(meta.get("created_at", "")),
            "path": str(path),
        }

    def exists(self, ref: BlobRef | str) -> bool:
        try:
            return self._resolve_path(ref).exists()
        except FileNotFoundError:
            return False

    def delete(self, ref: BlobRef | str) -> None:
        hash_value = _ref_to_hash(ref)
        self._meta_path(hash_value).unlink(missing_ok=True)
        for path in self._owned_payload_paths(hash_value):
            path.unlink(missing_ok=True)

    def path_for(self, digest: str) -> str:
        hash_value = _normalize_hash(digest)
        try:
            return str(self._resolve_path(hash_value))
        except FileNotFoundError:
            ext = ""
            sidecar = self._meta_path(hash_value)
            if sidecar.exists():
                try:
                    parsed = json.loads(sidecar.read_text(encoding="utf-8"))
                    ext = _normalize_ext(parsed.get("ext"))
                except json.JSONDecodeError:
                    ext = ""
            return str(self._target_path(hash_value, ext=ext))

    def gc(self, policy: dict[str, Any] | None = None) -> dict[str, Any]:
        policy = policy or {}
        dry_run = bool(policy.get("dry_run", True))
        max_age_days = int(policy.get("max_age_days", -1))
        max_total_bytes = int(policy.get("max_total_bytes", -1))

        # Inventory owned payloads grouped by digest. Unrelated files that
        # merely share a digest's hash prefix (e.g. <digest>backup) have a
        # stem that is not the owned digest's hash, so they are excluded from
        # both candidate selection and byte accounting below.
        groups = self._owned_payload_groups()
        units: list[tuple[float, str, list[Path], int]] = []
        total_files = 0
        total_bytes = 0
        for hash_value, paths in groups.items():
            size = sum(int(p.stat().st_size) for p in paths)
            oldest = min(float(p.stat().st_mtime) for p in paths)
            total_files += len(paths)
            total_bytes += size
            units.append((oldest, hash_value, paths, size))

        selected: set[str] = set()
        if max_age_days >= 0:
            now_ts = time.time()
            for oldest, hash_value, _paths, _size in units:
                age_days = (now_ts - oldest) / 86_400
                if age_days >= float(max_age_days):
                    selected.add(hash_value)

        if max_total_bytes >= 0 and total_bytes > max_total_bytes:
            running = total_bytes
            for _oldest, hash_value, _paths, size in sorted(units):
                if running <= max_total_bytes:
                    break
                selected.add(hash_value)
                running -= size

        # Evict each selected digest's whole owned representation so the digest
        # is not left reachable through a sibling payload (e.g. a legacy
        # <hash>.bin alongside <hash>.txt). Selection and deletion both operate
        # at this same digest unit, so the byte accounting above agrees with
        # what is actually removed.
        evicting: list[Path] = []
        deleted_bytes = 0
        for hash_value in sorted(selected):
            owned = groups[hash_value]
            evicting.extend(owned)
            deleted_bytes += sum(int(item.stat().st_size) for item in owned)
            if not dry_run:
                for item in owned:
                    item.unlink(missing_ok=True)
                self._meta_path(hash_value).unlink(missing_ok=True)

        return {
            "dry_run": dry_run,
            "total_files": total_files,
            "total_bytes": total_bytes,
            "candidates": [str(path) for path in evicting],
            "deleted_files": 0 if dry_run else len(evicting),
            "deleted_bytes": 0 if dry_run else deleted_bytes,
        }

    def verify(self, digest: str) -> dict[str, Any]:
        hash_value = _normalize_hash(digest)
        path = self._resolve_path(hash_value)
        calculated = _hash_file(path)
        size = int(path.stat().st_size)
        meta_path = self._meta_path(hash_value)
        meta: dict[str, Any] | None = None
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = None
        return {
            "hash": hash_value,
            "calculated_hash": calculated,
            "matches": calculated == hash_value,
            "size": size,
            "path": str(path),
            "metadata": meta,
        }

    def healthcheck(self) -> dict[str, Any]:
        try:
            self.root_dir.stat()
            return {
                "ok": True,
                "type": "blob_fs",
                "root_accessible": True,
            }
        except Exception as exc:
            return {
                "ok": False,
                "type": "blob_fs",
                "error": str(exc),
            }

    def describe_backend(self) -> BackendDescriptor:
        return BackendDescriptor(
            backend_id="fs-blob-store",
            version=OPENMINION_VERSION,
            planes_supported={"blob"},
            capabilities={
                "atomic_write": True,
                "content_addressing": True,
                "garbage_collection": True,
                "metadata_storage": True,
                "cross_platform": True,
            },
            limits={
                "max_blob_size_mb": 1024,
                "max_path_length": 255,
            },
        )

    def _existing_ref(self, hash_value: str) -> BlobRef | None:
        sidecar = self._meta_path(hash_value)
        if not sidecar.exists():
            return None
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        recorded_ext = _normalize_ext(meta.get("ext"))
        target = self._target_path(hash_value, ext=recorded_ext)
        if not target.exists():
            return None
        return BlobRef(
            algo="sha256",
            hash=hash_value,
            path=str(target),
            size=int(meta.get("size", target.stat().st_size)),
            media_type=str(meta.get("media_type", "application/octet-stream")),
            created_at=str(meta.get("created_at", "")),
            ext=recorded_ext or None,
        )

    def _write_meta(self, hash_value: str, payload: dict[str, Any]) -> None:
        sidecar = self._meta_path(hash_value)
        atomic_write_text(
            sidecar,
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ),
        )

    def _owned_payload_groups(self) -> dict[str, list[Path]]:
        """Map each owned digest hash to its owned payload paths.

        A file is an owned payload only when its stem equals a valid sha256
        digest hash. Unrelated files that merely share a digest's hash prefix
        (e.g. <digest>backup) and the .meta.json sidecar are excluded.
        """
        groups: dict[str, list[Path]] = {}
        for path in self.blob_root.rglob("*"):
            if not path.is_file() or path.name.endswith(".meta.json"):
                continue
            hash_value = path.stem
            if not _is_sha256_hash(hash_value):
                continue
            groups.setdefault(hash_value, []).append(path)
        return {hash_value: sorted(paths) for hash_value, paths in groups.items()}

    def _owned_payload_paths(self, hash_value: str) -> list[Path]:
        """Owned payload siblings for one digest, scoped to its shard.

        Lists only the shard directory derived from the digest's hash prefix
        and keeps files whose stem is the digest's own valid sha256 hash,
        excluding sidecars and unrelated hash-prefix files (e.g.
        <digest>backup). This resolves the siblings locally instead of
        walking the whole store on every single-digest delete.
        """
        shard = self.blob_root / hash_value[:2]
        if not shard.is_dir():
            return []
        return sorted(
            path
            for path in shard.iterdir()
            if path.is_file()
            and not path.name.endswith(".meta.json")
            and path.stem == hash_value
            and _is_sha256_hash(path.stem)
        )

    def _resolve_path(self, ref: BlobRef | str) -> Path:
        if isinstance(ref, BlobRef):
            return Path(ref.path)
        hash_value = _normalize_hash(ref)
        prefix = hash_value[:2]
        root = self.blob_root / prefix
        matches = sorted(
            path
            for path in root.glob(f"{hash_value}*")
            if path.is_file() and not path.name.endswith(".meta.json")
        )
        if not matches:
            raise FileNotFoundError(f"blob not found: {hash_value}")
        return matches[0]

    def _target_path(self, hash_value: str, *, ext: str) -> Path:
        prefix = hash_value[:2]
        suffix = f".{ext}" if ext else ""
        return self.blob_root / prefix / f"{hash_value}{suffix}"

    def _meta_path(self, hash_value: str) -> Path:
        prefix = hash_value[:2]
        return self.blob_root / prefix / f"{hash_value}.meta.json"


def _is_sha256_hash(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _normalize_hash(value: str) -> str:
    text = str(value or "").strip().lower()
    if not _is_sha256_hash(text):
        raise ValueError(f"invalid sha256 hash: {value}")
    return text


def _normalize_ext(value: str | None) -> str:
    text = str(value or "").strip().lower().lstrip(".")
    return "".join(ch for ch in text if ch.isalnum())


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _ref_to_hash(ref: BlobRef | str) -> str:
    if isinstance(ref, BlobRef):
        return ref.hash
    return _normalize_hash(ref)


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
