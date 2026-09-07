from __future__ import annotations

import queue
import threading
from datetime import datetime, timedelta, timezone

import pytest

from openminion.modules.artifact.control import ArtifactCtl

from .utils import make_config


def test_concurrent_ingest_same_sha(tmp_path) -> None:
    config = make_config(tmp_path)
    payload = b"shared-payload"
    barrier = threading.Barrier(2)
    refs: queue.Queue[str] = queue.Queue()
    errors: queue.Queue[BaseException] = queue.Queue()

    def _ingest() -> None:
        try:
            ctl = ArtifactCtl(config)
            barrier.wait(timeout=5)
            ref = ctl.ingest_bytes(payload, original_name="shared.txt")
            refs.put(ref.sha256)
        except BaseException as exc:  # pragma: no cover - failure path
            errors.put(exc)
        finally:
            if "ctl" in locals():
                ctl.close()

    threads = [threading.Thread(target=_ingest) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors.empty(), list(errors.queue)
    assert refs.qsize() == 2
    assert refs.get_nowait() == refs.get_nowait()

    with ArtifactCtl(config) as ctl:
        count = ctl.index._conn.execute(  # type: ignore[attr-defined]
            "SELECT count(*) FROM artifacts WHERE original_name = ?",
            ("shared.txt",),
        ).fetchone()[0]
        assert count == 1


def test_verify_after_purge_reports_clean(tmp_path) -> None:
    with ArtifactCtl(make_config(tmp_path)) as ctl:
        ref = ctl.ingest_bytes(b"purge-me", original_name="purge.txt")
        ctl.ensure_view(ref.sha256, "digest")
        ctl.delete(ref.sha256, soft=True)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn = ctl.index._conn  # type: ignore[attr-defined]
        with conn:
            conn.execute(
                "UPDATE artifacts SET deleted_at = ? WHERE deleted_at IS NOT NULL",
                (old_ts,),
            )
            conn.execute(
                "UPDATE artifact_views SET deleted_at = ? WHERE deleted_at IS NOT NULL",
                (old_ts,),
            )

        ctl.purge(grace_days=0)
        report = ctl.verify()

        assert report.checked == 0
        assert report.failed == 0
        assert report.issues == []


def test_shared_view_survives_other_raw_artifact_purge(tmp_path) -> None:
    with ArtifactCtl(make_config(tmp_path)) as ctl:
        first = ctl.ingest_bytes(b'{"a": 1}', mime="application/json")
        second = ctl.ingest_bytes(b'{ "a": 1 }', mime="application/json")
        first_view = ctl.ensure_view(first.sha256, "json")
        second_view = ctl.ensure_view(second.sha256, "json")
        assert first_view.sha256 == second_view.sha256

        ctl.delete(first.sha256, soft=True)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn = ctl.index._conn  # type: ignore[attr-defined]
        with conn:
            conn.execute(
                "UPDATE artifacts SET deleted_at = ? WHERE deleted_at IS NOT NULL",
                (old_ts,),
            )
            conn.execute(
                "UPDATE artifact_views SET deleted_at = ? WHERE deleted_at IS NOT NULL",
                (old_ts,),
            )

        ctl.purge(grace_days=0)

        assert ctl.read_view(second.sha256, "json") == {"a": 1}
        assert ctl.index.get_artifact(second_view.sha256, include_deleted=False)
        assert ctl.verify().failed == 0


def test_hard_delete_preserves_raw_sha_used_as_another_active_view(tmp_path) -> None:
    overrides = {"artifactctl": {"views": {"auto_generate": []}}}
    with ArtifactCtl(make_config(tmp_path, overrides)) as ctl:
        shared = ctl.ingest_bytes(b'{\n  "a": 1\n}', mime="application/json")
        raw = ctl.ingest_bytes(b'{"a":1}', mime="application/json")
        assert ctl.ensure_view(raw.sha256, "json").sha256 == shared.sha256

        ctl.delete(shared.sha256, soft=False)

        assert ctl.blob_store.exists(shared.sha256)
        assert ctl.get(shared.sha256).deleted_at is None
        assert ctl.ensure_view(raw.sha256, "json").sha256 == shared.sha256


@pytest.mark.parametrize("protection", ["alias", "reference"])
def test_view_blob_survives_direct_protection(tmp_path, protection: str) -> None:
    with ArtifactCtl(make_config(tmp_path)) as ctl:
        raw = ctl.ingest_bytes(b'{"a": 1}', mime="application/json")
        view = ctl.ensure_view(raw.sha256, "json")
        if protection == "alias":
            ctl.alias_set("kept-view", view.sha256)
        else:
            ctl.ref_add("session", "kept-view", view.sha256)

        ctl.delete(raw.sha256, soft=True)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn = ctl.index._conn  # type: ignore[attr-defined]
        with conn:
            conn.execute(
                "UPDATE artifacts SET deleted_at = ? WHERE deleted_at IS NOT NULL",
                (old_ts,),
            )
            conn.execute(
                "UPDATE artifact_views SET deleted_at = ? WHERE deleted_at IS NOT NULL",
                (old_ts,),
            )

        ctl.purge(grace_days=0)

        assert ctl.read_bytes(view.sha256)
        assert ctl.index.get_artifact(view.sha256, include_deleted=False)
        assert ctl.verify().failed == 0
