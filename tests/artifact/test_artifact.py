from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.artifact.errors import ArtifactCtlError


def _cfg(tmp_path: Path) -> dict:
    root = tmp_path / ".openminion" / "artifact-store"
    return {
        "artifactctl": {
            "blob_store": {
                "backend": "filesystem_cas",
                "root_dir": str(root),
                "max_ingest_bytes": 104_857_600,
            },
            "index": {
                "backend": "sqlite",
                "sqlite_path": str(root / "index.db"),
                "wal": False,
            },
            "views": {
                "auto_generate": ["digest", "text"],
                "table_max_rows": 200,
                "digest_max_chars": 2000,
                "digest_max_lines": 80,
                "json_max_chars": 20000,
            },
            "aliases": {
                "expire_default_days": 0,
            },
            "retention": {
                "keep_days": 14,
                "delete_unreferenced_after_days": 7,
                "purge_grace_days": 7,
                "delete_views_first": True,
            },
            "security": {
                "store_original_path": False,
                "redaction_enabled": True,
            },
        }
    }


def test_ingest_views_and_aliases(tmp_path: Path) -> None:
    ctl = ArtifactCtl(_cfg(tmp_path))
    try:
        a = ctl.ingest_bytes(
            b"hello world\n", original_name="hello.txt", label="greeting"
        )
        b = ctl.ingest_bytes(b"hello world\n", original_name="duplicate.txt")

        assert a.sha256 == b.sha256

        meta = ctl.get(a.sha256)
        assert meta.label in {"greeting", None}

        text_view = ctl.read_view(a.sha256, "text")
        assert isinstance(text_view, str)
        assert "hello world" in text_view

        digest = ctl.read_digest(a.sha256)
        assert digest["artifact_sha256"] == a.sha256
        assert "excerpt" in digest

        ctl.alias_set("session:s1/latest", a.sha256)
        resolved = ctl.alias_resolve("session:s1/latest")
        assert resolved is not None
        assert resolved.sha256 == a.sha256
    finally:
        ctl.close()


def test_json_and_table_views(tmp_path: Path) -> None:
    ctl = ArtifactCtl(_cfg(tmp_path))
    try:
        j = ctl.ingest_bytes(
            b'{"a":1,"b":2}', mime="application/json", original_name="data.json"
        )
        j_view = ctl.read_view(j.sha256, "json")
        assert isinstance(j_view, dict)
        assert j_view["a"] == 1

        c = ctl.ingest_bytes(
            b"c1,c2\n1,2\n3,4\n", mime="text/csv", original_name="table.csv"
        )
        t_view = ctl.read_view(c.sha256, "table")
        assert isinstance(t_view, dict)
        assert t_view["columns"] == ["c1", "c2"]
        assert t_view["sampled_rows"] == 2
    finally:
        ctl.close()


def test_gc_and_purge_are_reference_aware(tmp_path: Path) -> None:
    ctl = ArtifactCtl(_cfg(tmp_path))
    try:
        keep_ref = ctl.ingest_bytes(b"keep", original_name="keep.txt")
        drop_ref = ctl.ingest_bytes(b"drop", original_name="drop.txt")

        ctl.ref_add("session", "s1", keep_ref.sha256)

        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn = ctl.index._conn  # type: ignore[attr-defined]
        with conn:
            conn.execute("UPDATE artifacts SET created_at = ?", (old_ts,))

        report = ctl.gc(plan_only=False, keep_days=0, delete_unreferenced_after_days=0)
        assert drop_ref.sha256 in report.candidates
        assert keep_ref.sha256 not in report.candidates

        dropped = ctl.get(drop_ref.sha256)
        kept = ctl.get(keep_ref.sha256)
        assert dropped.deleted_at is not None
        assert kept.deleted_at is None

        purge = ctl.purge(grace_days=0)
        assert purge.purged_blobs >= 1

        keep_path = Path(ctl.blob_store.path_for(keep_ref.sha256))
        drop_path = Path(ctl.blob_store.path_for(drop_ref.sha256))
        assert keep_path.exists()
        assert not drop_path.exists()
    finally:
        ctl.close()


def test_verify_detects_corruption(tmp_path: Path) -> None:
    ctl = ArtifactCtl(_cfg(tmp_path))
    try:
        ref = ctl.ingest_bytes(b"stable data", original_name="stable.txt")
        blob_path = Path(ctl.blob_store.path_for(ref.sha256))
        blob_path.write_bytes(b"corrupted")

        report = ctl.verify(ref.sha256)
        assert report.checked == 1
        assert report.failed == 1
        assert report.issues[0].issue == "digest_mismatch"
    finally:
        ctl.close()


def test_soft_deleted_artifact_metadata_remains_but_payload_is_unreadable(
    tmp_path: Path,
) -> None:
    with ArtifactCtl(_cfg(tmp_path)) as ctl:
        ref = ctl.ingest_bytes(b"payload", original_name="payload.txt")

        ctl.delete(ref.sha256)

        assert ctl.get(ref.sha256).deleted_at is not None
        with pytest.raises(ArtifactCtlError) as exc:
            ctl.read_bytes(ref.sha256)
        assert exc.value.code == "NOT_FOUND"


def test_payload_access_requires_metadata_row_even_when_blob_exists(
    tmp_path: Path,
) -> None:
    with ArtifactCtl(_cfg(tmp_path)) as ctl:
        data = b"orphan blob"
        sha256 = hashlib.sha256(data).hexdigest()
        ctl.blob_store.put_bytes(sha256, data)

        with pytest.raises(ArtifactCtlError) as open_error:
            ctl.open(sha256)
        assert open_error.value.code == "NOT_FOUND"

        with pytest.raises(ArtifactCtlError) as read_error:
            ctl.read_bytes(sha256)
        assert read_error.value.code == "NOT_FOUND"


def test_artifactctl_context_manager(tmp_path: Path) -> None:
    conn = None
    with ArtifactCtl(_cfg(tmp_path)) as ctl:
        conn = ctl.index._conn  # type: ignore[attr-defined]
        ctl.ingest_bytes(b"hello", original_name="hello.txt")

    assert conn is not None
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
