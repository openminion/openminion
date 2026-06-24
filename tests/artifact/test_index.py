from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openminion.modules.artifact.models import ViewRecord, iso_now
from openminion.modules.artifact.storage import SQLiteArtifactIndex

from .utils import make_artifact_meta


def _db(tmp_path):
    return tmp_path / "index.db"


def _make_index(tmp_path):
    return SQLiteArtifactIndex(_db(tmp_path), wal=False)


@pytest.fixture
def idx(tmp_path):
    index = _make_index(tmp_path)
    try:
        yield index
    finally:
        index.close()


def test_upsert_and_get_artifact_respects_deleted_flag(idx):
    meta = make_artifact_meta("a" * 64, original_name="keep.txt")
    idx.upsert_artifact(meta)

    fetched = idx.get_artifact(meta.sha256, include_deleted=False)
    assert fetched is not None
    assert fetched.original_name == "keep.txt"

    idx.soft_delete_artifacts([meta.sha256], iso_now())
    assert idx.get_artifact(meta.sha256, include_deleted=False) is None
    assert idx.get_artifact(meta.sha256, include_deleted=True) is not None


def test_list_recent_search_and_largest(idx):
    older = make_artifact_meta(
        "b" * 64,
        original_name="old.bin",
        created_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        size_bytes=10,
        label="old",
        mime="text/plain",
    )
    newer = make_artifact_meta(
        "c" * 64,
        original_name="new.bin",
        created_at=datetime.now(timezone.utc).isoformat(),
        size_bytes=100,
        label="new",
        session_id="s1",
    )
    idx.upsert_artifact(older)
    idx.upsert_artifact(newer)

    recent = idx.list_recent(limit=1)
    assert len(recent) == 1 and recent[0].sha256 == newer.sha256

    results = idx.search("new", filters={"session_id": "s1"})
    assert len(results) == 1 and results[0].sha256 == newer.sha256

    largest = idx.largest(limit=1)
    assert len(largest) == 1 and largest[0].sha256 == newer.sha256


def test_missing_view_type_filter(idx):
    target = make_artifact_meta("d" * 64)
    idx.upsert_artifact(target)
    view = ViewRecord(
        raw_sha256=target.sha256,
        view_type="digest",
        schema_version="v1",
        policy_hash="",
        view_sha256="e" * 64,
        view_path=None,
        mime="application/json",
        size_bytes=10,
        created_at=target.created_at,
    )
    idx.upsert_view(view)

    missing = idx.list_recent(filters={"missing_view_type": "text"})
    assert len(missing) == 1
    assert missing[0].sha256 == target.sha256

    none = idx.list_recent(filters={"missing_view_type": "digest"})
    assert all(item.sha256 != target.sha256 for item in none)


def test_alias_and_reference_sets(idx):
    meta = make_artifact_meta("f" * 64)
    idx.upsert_artifact(meta)

    idx.alias_set("alias:latest", meta.sha256)
    aliases = idx.alias_list()
    assert aliases and aliases[0].sha256 == meta.sha256
    assert meta.sha256 in idx.active_alias_shas()

    idx.add_reference("session", "s1", meta.sha256)
    assert meta.sha256 in idx.active_reference_shas()

    idx.remove_reference("session", "s1", meta.sha256)
    assert meta.sha256 not in idx.active_reference_shas()

    idx.alias_delete("alias:latest")
    assert not idx.alias_list()
