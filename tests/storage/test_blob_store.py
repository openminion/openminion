import hashlib
import json
import os
import time

from openminion.modules.storage.backends.blob_store import BlobStoreFS


def _payload_dir(tmp_path, digest):
    return tmp_path / "artifacts" / "sha256" / digest[:2]


def _payloads(tmp_path, digest):
    return [
        p
        for p in _payload_dir(tmp_path, digest).glob(f"{digest}*")
        if p.is_file() and not p.name.endswith(".meta.json")
    ]


def test_blob_verify_roundtrip(tmp_path):
    store = BlobStoreFS(tmp_path)
    ref = store.put_bytes(b"hello world", media_type="text/plain")

    result = store.verify(ref.hash)

    assert result["matches"] is True
    assert result["hash"] == ref.hash
    assert result["size"] == ref.size
    assert result["metadata"]["media_type"] == "text/plain"


def test_second_put_same_ext_is_idempotent(tmp_path):
    store = BlobStoreFS(tmp_path)
    ref1 = store.put_bytes(b"hello world", media_type="text/plain", ext="txt")
    ref2 = store.put_bytes(b"hello world", media_type="text/plain", ext="txt")

    assert ref2.hash == ref1.hash
    assert ref2.path == ref1.path
    assert ref2.ext == "txt"
    assert ref2.media_type == "text/plain"
    assert ref2.created_at == ref1.created_at

    payloads = _payloads(tmp_path, ref1.hash)
    assert len(payloads) == 1

    sidecar = _payload_dir(tmp_path, ref1.hash) / f"{ref1.hash}.meta.json"
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert meta["created_at"] == ref1.created_at
    assert meta["ext"] == "txt"


def test_second_put_different_ext_reuses_existing(tmp_path):
    store = BlobStoreFS(tmp_path)
    ref1 = store.put_bytes(b"hello world", media_type="text/plain", ext="txt")
    ref2 = store.put_bytes(b"hello world", media_type="application/json", ext="json")

    assert ref2.hash == ref1.hash
    assert ref2.path == ref1.path
    assert ref2.ext == "txt"
    assert ref2.media_type == "text/plain"
    assert ref2.created_at == ref1.created_at

    payloads = _payloads(tmp_path, ref1.hash)
    assert len(payloads) == 1
    assert payloads[0].suffix == ".txt"
    assert not (_payload_dir(tmp_path, ref1.hash) / f"{ref1.hash}.json").exists()


def test_open_stat_verify_by_digest_before_delete(tmp_path):
    store = BlobStoreFS(tmp_path)
    ref = store.put_bytes(b"hello world", media_type="text/plain", ext="txt")
    digest = ref.hash

    with store.open(digest) as fh:
        assert fh.read() == b"hello world"

    st = store.stat(digest)
    assert st["hash"] == digest
    assert st["size"] == len(b"hello world")

    assert store.verify(digest)["matches"] is True


def test_delete_removes_payload_and_sidecar(tmp_path):
    store = BlobStoreFS(tmp_path)
    ref = store.put_bytes(b"hello world", media_type="text/plain", ext="txt")
    digest = ref.hash

    store.delete(digest)

    assert _payloads(tmp_path, digest) == []
    assert not (_payload_dir(tmp_path, digest) / f"{digest}.meta.json").exists()


def test_delete_legacy_duplicate_files(tmp_path):
    store = BlobStoreFS(tmp_path)
    data = b"hello world"
    digest = hashlib.sha256(data).hexdigest()
    payload_dir = _payload_dir(tmp_path, digest)
    payload_dir.mkdir(parents=True, exist_ok=True)
    (payload_dir / f"{digest}.txt").write_bytes(data)
    (payload_dir / f"{digest}.json").write_bytes(data)
    (payload_dir / f"{digest}.meta.json").write_text(
        json.dumps(
            {
                "algo": "sha256",
                "hash": digest,
                "media_type": "text/plain",
                "ext": "txt",
                "size": len(data),
                "created_at": "2020-01-01T00:00:00Z",
                "meta": {},
            }
        ),
        encoding="utf-8",
    )

    store.delete(digest)

    assert _payloads(tmp_path, digest) == []
    assert not (payload_dir / f"{digest}.txt").exists()
    assert not (payload_dir / f"{digest}.json").exists()
    assert not (payload_dir / f"{digest}.meta.json").exists()


def test_legacy_duplicate_files_read_before_delete(tmp_path):
    store = BlobStoreFS(tmp_path)
    data = b"hello world"
    digest = hashlib.sha256(data).hexdigest()
    payload_dir = _payload_dir(tmp_path, digest)
    payload_dir.mkdir(parents=True, exist_ok=True)
    (payload_dir / f"{digest}.txt").write_bytes(data)
    (payload_dir / f"{digest}.json").write_bytes(data)
    (payload_dir / f"{digest}.meta.json").write_text(
        json.dumps(
            {
                "algo": "sha256",
                "hash": digest,
                "media_type": "text/plain",
                "ext": "txt",
                "size": len(data),
                "created_at": "2020-01-01T00:00:00Z",
                "meta": {},
            }
        ),
        encoding="utf-8",
    )

    with store.open(digest) as fh:
        assert fh.read() == data

    st = store.stat(digest)
    assert st["hash"] == digest
    assert st["size"] == len(data)


def test_gc_evicts_legacy_duplicate_payloads_together(tmp_path):
    store = BlobStoreFS(tmp_path)
    data = b"hello"
    ref = store.put_bytes(data, media_type="text/plain", ext="txt")
    digest = ref.hash
    payload_dir = _payload_dir(tmp_path, digest)
    (payload_dir / f"{digest}.bin").write_bytes(data)

    now = time.time()
    os.utime(payload_dir / f"{digest}.txt", (now, now))
    os.utime(payload_dir / f"{digest}.bin", (now - 100, now - 100))

    result = store.gc({"max_total_bytes": 5, "dry_run": False})

    assert result["dry_run"] is False
    assert result["deleted_files"] == 2
    assert result["deleted_bytes"] == 2 * len(data)
    assert _payloads(tmp_path, digest) == []
    assert not (payload_dir / f"{digest}.meta.json").exists()

    # A subsequent put of the same bytes with a different ext must not
    # recreate duplicate payloads.
    ref2 = store.put_bytes(data, media_type="application/json", ext="json")
    assert ref2.hash == digest
    assert ref2.ext == "json"
    payloads = _payloads(tmp_path, digest)
    assert len(payloads) == 1
    assert payloads[0].suffix == ".json"


def test_gc_dry_run_reports_owned_payloads_without_deleting(tmp_path):
    store = BlobStoreFS(tmp_path)
    data = b"hello"
    ref = store.put_bytes(data, media_type="text/plain", ext="txt")
    digest = ref.hash
    payload_dir = _payload_dir(tmp_path, digest)
    (payload_dir / f"{digest}.bin").write_bytes(data)

    now = time.time()
    os.utime(payload_dir / f"{digest}.txt", (now, now))
    os.utime(payload_dir / f"{digest}.bin", (now - 100, now - 100))

    result = store.gc({"max_total_bytes": 5, "dry_run": True})

    assert result["dry_run"] is True
    assert result["deleted_files"] == 0
    assert result["deleted_bytes"] == 0
    assert set(result["candidates"]) == {
        str(payload_dir / f"{digest}.txt"),
        str(payload_dir / f"{digest}.bin"),
    }
    assert len(_payloads(tmp_path, digest)) == 2
    assert (payload_dir / f"{digest}.meta.json").exists()


def test_gc_excludes_unrelated_hash_prefix_files(tmp_path):
    store = BlobStoreFS(tmp_path)
    data = b"hello"
    ref = store.put_bytes(data, media_type="text/plain", ext="txt")
    digest = ref.hash
    payload_dir = _payload_dir(tmp_path, digest)
    unrelated = payload_dir / f"{digest}backup"
    unrelated.write_bytes(b"xxxxx")

    now = time.time()
    os.utime(payload_dir / f"{digest}.txt", (now - 200, now - 200))
    os.utime(unrelated, (now, now))

    store.gc({"max_total_bytes": 5, "dry_run": False})

    assert not (payload_dir / f"{digest}.txt").exists()
    assert unrelated.exists()
    assert not (payload_dir / f"{digest}.meta.json").exists()


def test_put_file_repeat_preserves_first_write_metadata(tmp_path):
    store = BlobStoreFS(tmp_path)
    src = tmp_path / "hello.txt"
    src.write_bytes(b"hello world")

    ref1 = store.put_file(src, media_type="text/plain", meta={"note": "first"})
    ref2 = store.put_file(src, media_type="text/plain", meta={"note": "second"})

    assert ref2.hash == ref1.hash
    assert ref2.path == ref1.path
    assert ref2.ext == ref1.ext
    assert ref2.media_type == ref1.media_type
    assert ref2.created_at == ref1.created_at

    payloads = _payloads(tmp_path, ref1.hash)
    assert len(payloads) == 1

    sidecar = _payload_dir(tmp_path, ref1.hash) / f"{ref1.hash}.meta.json"
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert meta["created_at"] == ref1.created_at
    assert meta["meta"] == {"note": "first"}
