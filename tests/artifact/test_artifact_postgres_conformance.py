from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

import pytest

from openminion.modules.artifact.models import ArtifactMeta, ViewRecord, iso_now
from openminion.modules.artifact.storage import build_artifact_index
from openminion.modules.artifact.storage.store import (
    PostgresArtifactIndex,
    SQLiteArtifactIndex,
)
from openminion.modules.storage.engine import StorageEngineConfig
from tests.storage.postgres_test_utils import (
    build_postgres_storage_config,
    open_postgres_record_store,
)


def _backend_params():
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", marks=pytest.mark.postgres, id="postgres"),
    ]


def _artifact(sha: str = "a" * 64) -> ArtifactMeta:
    return ArtifactMeta(
        sha256=sha,
        size_bytes=12,
        mime="text/plain",
        created_at=iso_now(),
        original_name="note.txt",
        original_path="/tmp/note.txt",
        label="note",
        session_id="sess-1",
        trace_id="trace-1",
        agent_id="agent-1",
        encoding="utf-8",
        deleted_at=None,
        meta_json={"kind": "text"},
    )


@pytest.fixture(params=_backend_params())
def artifact_index_case(request: pytest.FixtureRequest, tmp_path: Path):
    backend = str(request.param)
    with ExitStack() as stack:
        if backend == "sqlite":
            index = SQLiteArtifactIndex(tmp_path / "artifact.db")
            stack.callback(index.close)
        else:
            record_store, _schema_name = stack.enter_context(
                open_postgres_record_store("mpt2_artifact")
            )
            index = PostgresArtifactIndex(record_store=record_store)
        yield backend, index


def test_artifact_index_round_trip(artifact_index_case) -> None:
    _backend, index = artifact_index_case
    meta = _artifact()
    index.upsert_artifact(meta)

    loaded = index.get_artifact(meta.sha256, include_deleted=False)
    assert loaded is not None
    assert loaded.meta_json == {"kind": "text"}
    assert index.list_recent(limit=5)[0].sha256 == meta.sha256
    assert index.search("note")[0].sha256 == meta.sha256
    assert index.largest(limit=5)[0].sha256 == meta.sha256

    view = ViewRecord(
        raw_sha256=meta.sha256,
        view_type="digest",
        schema_version="v1",
        policy_hash="",
        view_sha256="b" * 64,
        view_path="/tmp/digest.txt",
        mime="text/plain",
        size_bytes=7,
        created_at=iso_now(),
        deleted_at=None,
    )
    index.upsert_view(view)
    assert index.get_view(meta.sha256, "digest", "v1") is not None
    assert index.list_views(meta.sha256)[0].view_type == "digest"

    index.alias_set("latest-note", meta.sha256, meta_json={"scope": "test"})
    alias = index.alias_resolve("latest-note")
    assert alias is not None
    assert alias.meta_json == {"scope": "test"}
    assert index.alias_list("latest")[0].sha256 == meta.sha256

    index.add_reference("turn", "turn-1", meta.sha256)
    assert index.active_reference_shas() == {meta.sha256}
    index.remove_reference("turn", "turn-1", meta.sha256)

    assert meta.sha256 in index.active_alias_shas()
    assert meta.sha256 in index.recent_artifact_shas(keep_days=30)
    assert index.eligible_for_gc(older_than_days=0, protected={meta.sha256}) == []
    assert (
        index.soft_delete_views_for_raw(meta.sha256, "2026-04-02T00:00:00+00:00") == 1
    )
    assert index.soft_delete_artifacts([meta.sha256], "2026-04-02T00:00:00+00:00") == 1
    assert index.purgeable_views(grace_days=0)[0].raw_sha256 == meta.sha256
    assert index.purgeable_artifacts(grace_days=0)[0].sha256 == meta.sha256
    assert len(index.all_artifacts(include_deleted=True)) == 1


def test_build_artifact_index_returns_sqlite_store(tmp_path: Path) -> None:
    index = build_artifact_index(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "artifact.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        database_path=tmp_path / "artifact.db",
    )
    try:
        assert isinstance(index, SQLiteArtifactIndex)
    finally:
        index.close()


@pytest.mark.postgres
def test_build_artifact_index_returns_postgres_store(tmp_path: Path) -> None:
    with open_postgres_record_store("mpt2_artifact_factory") as (
        _record_store,
        schema_name,
    ):
        index = build_artifact_index(
            config=build_postgres_storage_config(
                tmp_path=tmp_path,
                schema_name=schema_name,
                sqlite_name="artifact.db",
            ),
            database_path=tmp_path / "artifact.db",
        )
        try:
            assert isinstance(index, PostgresArtifactIndex)
        finally:
            index.close()
