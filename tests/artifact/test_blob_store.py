from __future__ import annotations

import hashlib

from openminion.modules.artifact.storage import FileSystemCASBlobStore


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_put_bytes_is_idempotent(tmp_path):
    store = FileSystemCASBlobStore(tmp_path)
    data = b"hello world"
    sha = _sha(data)

    store.put_bytes(sha, data)
    store.put_bytes(sha, b"corrupted")

    blob_path = store.path_for(sha)
    with open(blob_path, "rb") as fh:
        assert fh.read() == data


def test_put_file_writes_once(tmp_path):
    store = FileSystemCASBlobStore(tmp_path)
    src = tmp_path / "source.bin"
    content = b"source payload"
    src.write_bytes(content)
    sha = _sha(content)

    store.put_file(sha, src)
    store.put_file(sha, src)

    assert store.exists(sha)
    with open(store.path_for(sha), "rb") as fh:
        assert fh.read() == content


def test_delete_removes_blob(tmp_path):
    store = FileSystemCASBlobStore(tmp_path)
    data = b"delete me"
    sha = _sha(data)
    store.put_bytes(sha, data)

    store.delete(sha)
    assert not store.exists(sha)
