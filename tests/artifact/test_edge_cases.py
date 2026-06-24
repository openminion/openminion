from __future__ import annotations

import queue
import threading
from datetime import datetime, timedelta, timezone

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
            conn.execute("UPDATE artifacts SET deleted_at = ?", (old_ts,))
            conn.execute("UPDATE artifact_views SET deleted_at = ?", (old_ts,))

        ctl.purge(grace_days=0)
        report = ctl.verify()

        assert report.checked == 0
        assert report.failed == 0
        assert report.issues == []
