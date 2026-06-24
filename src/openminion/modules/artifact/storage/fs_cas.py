from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

from openminion.modules.artifact.storage.base import BlobStore
from openminion.modules.storage.backends.blob_store import BlobStoreFS


class FileSystemCASBlobStore(BlobStore):
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve(strict=False)
        self._core = BlobStoreFS(self.root_dir)

    def put_bytes(self, sha256: str, data: bytes) -> None:
        if self._core.exists(sha256):
            return
        digest = hashlib.sha256(data).hexdigest()
        if digest != sha256:
            raise ValueError("sha256 mismatch for put_bytes payload")
        self._core.put_known_hash_bytes(sha256, data)

    def put_file(self, sha256: str, path: str | Path) -> None:
        if self._core.exists(sha256):
            return
        src = Path(path).expanduser().resolve(strict=True)
        digest = _hash_file(src)
        if digest != sha256:
            raise ValueError("sha256 mismatch for put_file payload")
        self._core.put_known_hash_file(sha256, src)

    def get_stream(self, sha256: str) -> BinaryIO:
        return self._core.open(sha256)

    def exists(self, sha256: str) -> bool:
        return self._core.exists(sha256)

    def delete(self, sha256: str) -> None:
        self._core.delete(sha256)

    def path_for(self, sha256: str) -> str:
        return self._core.path_for(sha256)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
