from openminion.modules.storage.backends.blob_store import BlobStoreFS


def test_blob_verify_roundtrip(tmp_path):
    store = BlobStoreFS(tmp_path)
    ref = store.put_bytes(b"hello world", media_type="text/plain")

    result = store.verify(ref.hash)

    assert result["matches"] is True
    assert result["hash"] == ref.hash
    assert result["size"] == ref.size
    assert result["metadata"]["media_type"] == "text/plain"
