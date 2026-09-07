from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.memory.models import (
    ArtifactRef as MemoryArtifactRef,
    MemoryRecord,
)
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore

from .utils import artifact_ctl


def _age_all_artifacts(ctl: ArtifactCtl, days: int) -> None:
    old_ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = ctl.index._conn  # type: ignore[attr-defined]
    with conn:
        conn.execute("UPDATE artifacts SET created_at = ?", (old_ts,))
        conn.execute("UPDATE artifact_views SET created_at = ?", (old_ts,))


def test_gc_plan_only_does_not_mark_deleted(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        ref = ctl.ingest_bytes(b"data", original_name="data.bin")
        report = ctl.gc(plan_only=True, keep_days=0, delete_unreferenced_after_days=0)
        assert ref.sha256 not in report.candidates
        assert ctl.get(ref.sha256).deleted_at is None


def test_gc_marks_unprotected_and_respects_references(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        keep_ref = ctl.ingest_bytes(b"keep", original_name="keep.txt")
        drop_ref = ctl.ingest_bytes(b"drop", original_name="drop.txt")
        ctl.ref_add("session", "s1", keep_ref.sha256)
        _age_all_artifacts(ctl, 30)
        report = ctl.gc(plan_only=False, keep_days=0, delete_unreferenced_after_days=0)
        assert drop_ref.sha256 in report.candidates
        assert keep_ref.sha256 not in report.candidates
        assert ctl.get(drop_ref.sha256).deleted_at is not None
        assert ctl.get(keep_ref.sha256).deleted_at is None


def test_purge_deletes_blobs_and_counts_missing(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        ref = ctl.ingest_bytes(b"purge", original_name="purge.txt")
        view_ref = ctl.ensure_view(ref.sha256, "digest")
        ctl.delete(ref.sha256, soft=True)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn = ctl.index._conn  # type: ignore[attr-defined]
        with conn:
            conn.execute("UPDATE artifacts SET deleted_at = ?", (old_ts,))
            conn.execute("UPDATE artifact_views SET deleted_at = ?", (old_ts,))
        view_path = ctl.blob_store.path_for(view_ref.sha256)
        Path(view_path).unlink(missing_ok=True)
        report = ctl.purge(grace_days=0)
        assert report.purged_blobs >= 1
        assert report.purged_views >= 0
        assert report.missing_files >= 1


def test_purge_removes_index_rows(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        ref = ctl.ingest_bytes(b"purge", original_name="purge.txt")
        ctl.ensure_view(ref.sha256, "digest")
        ctl.delete(ref.sha256, soft=True)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn = ctl.index._conn  # type: ignore[attr-defined]
        with conn:
            conn.execute("UPDATE artifacts SET deleted_at = ?", (old_ts,))
            conn.execute("UPDATE artifact_views SET deleted_at = ?", (old_ts,))

        ctl.purge(grace_days=0)

        deleted_artifacts = conn.execute(
            "SELECT count(*) FROM artifacts WHERE deleted_at IS NOT NULL"
        ).fetchone()[0]
        deleted_views = conn.execute(
            "SELECT count(*) FROM artifact_views WHERE deleted_at IS NOT NULL"
        ).fetchone()[0]
        assert deleted_artifacts == 0
        assert deleted_views == 0


def test_purge_respects_delete_views_first_order(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        ref = ctl.ingest_bytes(b"purge", original_name="purge.txt")
        ctl.ensure_view(ref.sha256, "digest")
        ctl.delete(ref.sha256, soft=True)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn = ctl.index._conn  # type: ignore[attr-defined]
        with conn:
            conn.execute("UPDATE artifacts SET deleted_at = ?", (old_ts,))
            conn.execute("UPDATE artifact_views SET deleted_at = ?", (old_ts,))

        deleted: list[str] = []
        original_delete = ctl.blob_store.delete

        def _recording_delete(sha256: str) -> None:
            deleted.append(sha256)
            original_delete(sha256)

        ctl.blob_store.delete = _recording_delete  # type: ignore[method-assign]
        ctl.purge(grace_days=0)

        assert deleted
        assert deleted[0] != ref.sha256
        assert ref.sha256 in deleted

    reverse_root = tmp_path / ".openminion" / "reverse"
    overrides = {
        "artifactctl": {
            "blob_store": {"root_dir": str(reverse_root)},
            "index": {"sqlite_path": str(reverse_root / "index.db")},
            "retention": {"delete_views_first": False},
        }
    }
    with artifact_ctl(tmp_path, overrides) as ctl:
        ref = ctl.ingest_bytes(b"purge", original_name="purge.txt")
        ctl.ensure_view(ref.sha256, "digest")
        ctl.delete(ref.sha256, soft=True)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn = ctl.index._conn  # type: ignore[attr-defined]
        with conn:
            conn.execute("UPDATE artifacts SET deleted_at = ?", (old_ts,))
            conn.execute("UPDATE artifact_views SET deleted_at = ?", (old_ts,))

        deleted = []
        original_delete = ctl.blob_store.delete

        def _recording_delete_reverse(sha256: str) -> None:
            deleted.append(sha256)
            original_delete(sha256)

        ctl.blob_store.delete = _recording_delete_reverse  # type: ignore[method-assign]
        ctl.purge(grace_days=0)

        assert deleted
        assert deleted[0] == ref.sha256


def test_purge_compacts_expired_aliases_and_reference_tombstones(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        ref = ctl.ingest_bytes(b"metadata", original_name="metadata.txt")
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        recent_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        ctl.alias_set("old-alias", ref.sha256, expires_at=old_ts)
        ctl.alias_set("recent-alias", ref.sha256, expires_at=recent_ts)
        ctl.ref_add("session", "old-edge", ref.sha256)
        ctl.ref_add("session", "recent-edge", ref.sha256)
        ctl.ref_remove("session", "old-edge", ref.sha256)
        ctl.ref_remove("session", "recent-edge", ref.sha256)

        conn = ctl.index._conn  # type: ignore[attr-defined]
        with conn:
            conn.execute(
                "UPDATE reference_edges SET deleted_at = ? WHERE owner_id = ?",
                (old_ts, "old-edge"),
            )
            conn.execute(
                "UPDATE reference_edges SET deleted_at = ? WHERE owner_id = ?",
                (recent_ts, "recent-edge"),
            )

        ctl.purge(grace_days=7)

        aliases = {
            row[0] for row in conn.execute("SELECT alias FROM aliases").fetchall()
        }
        edges = {
            row[0]
            for row in conn.execute("SELECT owner_id FROM reference_edges").fetchall()
        }
        assert aliases == {"recent-alias"}
        assert edges == {"recent-edge"}


def test_purge_preserves_shared_view_until_all_mappings_leave_grace(tmp_path):
    overrides = {"artifactctl": {"views": {"auto_generate": []}}}
    with artifact_ctl(tmp_path, overrides) as ctl:
        first = ctl.ingest_bytes(b'{"b":2,"a":1}', mime="application/json")
        second = ctl.ingest_bytes(b'{ "a": 1, "b": 2 }', mime="application/json")
        shared_view = ctl.ensure_view(first.sha256, "json")
        assert ctl.ensure_view(second.sha256, "json").sha256 == shared_view.sha256
        assert shared_view.sha256 not in {first.sha256, second.sha256}

        ctl.delete(first.sha256)
        ctl.delete(second.sha256)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn = ctl.index._conn  # type: ignore[attr-defined]
        with conn:
            conn.execute(
                "UPDATE artifacts SET deleted_at = ? WHERE sha256 = ?",
                (old_ts, first.sha256),
            )
            conn.execute(
                "UPDATE artifact_views SET deleted_at = ? WHERE raw_sha256 = ?",
                (old_ts, first.sha256),
            )
            conn.execute(
                "UPDATE artifacts SET deleted_at = NULL WHERE sha256 = ?",
                (shared_view.sha256,),
            )

        ctl.purge(grace_days=7)

        assert ctl.blob_store.exists(shared_view.sha256)
        assert ctl.get(shared_view.sha256).deleted_at is None
        remaining = conn.execute(
            "SELECT count(*) FROM artifact_views WHERE raw_sha256 = ?",
            (second.sha256,),
        ).fetchone()[0]
        assert remaining == 1


def _memory_artifact_ref(ref: str) -> MemoryArtifactRef:
    return MemoryArtifactRef(
        ref=ref,
        mime="application/octet-stream",
        sha256=ref.rsplit("/", 1)[-1],
        size_bytes=1,
    )


def test_gc_plan_protects_session_turn_artifact_refs(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        keep_ref = ctl.ingest_bytes(b"keep", original_name="keep.txt")
        drop_ref = ctl.ingest_bytes(b"drop", original_name="drop.txt")
        store = SQLiteSessionStore(tmp_path / "sessions.db", artifactctl=ctl)
        try:
            session_id = store.create_session(session_id="sess-gc-protected")
            store.append_turn(
                session_id,
                role="user",
                content="see attachment",
                attachments=[keep_ref.ref, "mem://skip"],
            )

            _age_all_artifacts(ctl, 30)
            report = ctl.gc(
                plan_only=True,
                keep_days=0,
                delete_unreferenced_after_days=0,
            )

            assert keep_ref.sha256 not in report.candidates
            assert drop_ref.sha256 in report.candidates
        finally:
            store.close()


def test_gc_plan_changes_after_memory_owner_removal(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        keep_ref = ctl.ingest_bytes(b"memory", original_name="memory.txt")
        store = SQLiteMemoryStore(tmp_path / "memory.db", artifactctl=ctl)
        record = MemoryRecord(
            id="mem-gc-protected",
            scope="agent:main",
            type="fact",
            content="artifact-backed memory",
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
            evidence_refs=[_memory_artifact_ref(keep_ref.ref)],
        )
        store.put(record)

        _age_all_artifacts(ctl, 30)
        protected_report = ctl.gc(
            plan_only=True,
            keep_days=0,
            delete_unreferenced_after_days=0,
        )
        assert keep_ref.sha256 not in protected_report.candidates

        store.delete(record.id)

        eligible_report = ctl.gc(
            plan_only=True,
            keep_days=0,
            delete_unreferenced_after_days=0,
        )
        assert keep_ref.sha256 in eligible_report.candidates


def test_gc_plan_protects_canonical_ref_emitted_by_runtime_context(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        workspace = tmp_path / "workspace"
        run_root = tmp_path / "run"
        workspace.mkdir()
        run_root.mkdir()
        ctx = RuntimeContext(
            policy=Policy(raw={"workspace_root": str(workspace)}),
            workspace=workspace,
            run_root=run_root,
            scope="READ_ONLY",
            confirm=False,
            artifactctl=ctl,
        )
        ctx.session_id = "sess-runtime-cas"
        ctx.trace_id = "trace-runtime-cas"
        ctx.tool_name = "fetch.get"
        artifact = ctx.write_artifact(
            "artifacts/fetch/body.txt",
            b"hello",
            "text/plain",
            durable=True,
        )

        store = SQLiteSessionStore(tmp_path / "sessions.db", artifactctl=ctl)
        try:
            session_id = store.create_session(session_id="sess-runtime-cas")
            store.append_turn(
                session_id,
                role="assistant",
                content="fetched body",
                attachments=[artifact.canonical_ref],
            )

            _age_all_artifacts(ctl, 30)
            report = ctl.gc(
                plan_only=True,
                keep_days=0,
                delete_unreferenced_after_days=0,
            )

            assert artifact.canonical_ref is not None
            assert artifact.sha256 not in report.candidates
        finally:
            store.close()


def test_gc_plan_keeps_runtime_local_artifact_outside_owner_edge_contract(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        workspace = tmp_path / "workspace"
        run_root = tmp_path / "run"
        workspace.mkdir()
        run_root.mkdir()
        ctx = RuntimeContext(
            policy=Policy(raw={"workspace_root": str(workspace)}),
            workspace=workspace,
            run_root=run_root,
            scope="READ_ONLY",
            confirm=False,
            artifactctl=ctl,
        )
        artifact = ctx.write_artifact(
            "artifacts/weather/debug.json",
            b"{}",
            "application/json",
            durable=False,
        )

        store = SQLiteSessionStore(tmp_path / "sessions.db", artifactctl=ctl)
        try:
            session_id = store.create_session(session_id="sess-runtime-local")
            store.append_turn(
                session_id,
                role="assistant",
                content="debug dump",
                attachments=[artifact.path],
            )

            assert artifact.canonical_ref is None
            assert all(
                meta.sha256 != artifact.sha256 for meta in ctl.list_recent(limit=20)
            )
        finally:
            store.close()
