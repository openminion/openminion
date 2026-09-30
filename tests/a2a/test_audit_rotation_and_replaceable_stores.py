from __future__ import annotations

from pathlib import Path
import time
from datetime import datetime, timedelta, timezone

import pytest

from openminion.modules.a2a.artifacts import LocalArtifactStore
from openminion.modules.a2a.models import AuditRecord, Envelope
from openminion.modules.a2a.runtime import A2ARuntime
from openminion.modules.a2a.storage import (
    MemoryAuditStore,
    MemoryStateStore,
    SQLiteAuditStore,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


def test_sqlite_audit_rotation_archives_old_files(root: Path) -> None:
    audit_root = root / "audit"
    store = SQLiteAuditStore(
        audit_root,
        retention_days=1,
        archive_retention_days=30,
    )

    old_ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    old_record = AuditRecord(
        ts=old_ts,
        msg_id="old-msg",
        trace_id="trace-old",
        from_agent="a",
        to_agent="b",
        to_capability=None,
        type="call",
        method="m.old",
        status="FAILED",
        error_code="X",
        error_message="old",
    )
    store.append_audit(old_record)

    now_ts = datetime.now(timezone.utc).isoformat()
    fresh_record = AuditRecord(
        ts=now_ts,
        msg_id="new-msg",
        trace_id="trace-new",
        from_agent="a",
        to_agent="b",
        to_capability=None,
        type="call",
        method="m.new",
        status="SUCCESS",
    )
    store.append_audit(fresh_record)

    old_db = audit_root / f"{old_ts[:10]}.db"
    archived = audit_root / "archive" / f"{old_db.name}.gz"

    assert not old_db.exists()
    assert archived.exists()

    rows = store.query_audit({"trace_id": "trace-new", "limit": 10})
    assert len(rows) == 1
    assert rows[0].method == "m.new"


def test_runtime_works_with_memory_stores(root: Path) -> None:
    runtime = A2ARuntime(
        state_store=MemoryStateStore(),
        audit_store=MemoryAuditStore(),
        artifact_store=LocalArtifactStore(root / "artifacts"),
        recovery_stale_heartbeat_sec=5,
    )
    try:

        def handler(envelope: Envelope) -> dict:
            return {"echo": envelope.params}

        runtime.register_agent("echo", ["echo."], handler)

        call_req = Envelope.new(
            from_agent="tester",
            to_agent="echo",
            to_capability=None,
            type="call",
            method="echo.ping",
            params={"x": 1},
            idempotency_key="idem-call",
        )
        call_res = runtime.call(call_req)
        assert call_res.params["ok"] is True
        assert call_res.params["data"] == {"echo": {"x": 1}}

        job_req = Envelope.new(
            from_agent="tester",
            to_agent="echo",
            to_capability=None,
            type="job.start",
            method="echo.job",
            params={"x": 2},
            idempotency_key="idem-job",
        )
        task_id = runtime.job_start(job_req)

        deadline = time.time() + 3.0
        last_state = ""
        while time.time() < deadline:
            row = runtime.job_status(task_id)
            last_state = row.state
            if row.state in {"SUCCESS", "FAILED", "CANCELED"}:
                break
            time.sleep(0.01)

        assert last_state == "SUCCESS"
    finally:
        runtime.close()


def test_memory_audit_store_drops_rows_outside_retention() -> None:
    store = MemoryAuditStore(retention_days=1)
    for age_days, msg_id in ((3, "old"), (0, "recent")):
        store.append_audit(
            AuditRecord(
                ts=(datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat(),
                msg_id=msg_id,
                trace_id=f"trace-{msg_id}",
                from_agent="a",
                to_agent="b",
                to_capability=None,
                type="call",
                method="echo.ping",
                status="SUCCESS",
            )
        )

    assert [row.msg_id for row in store.query_audit()] == ["recent"]


def test_audit_failure_warns_without_payload_and_does_not_fail_work(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _FailingAuditStore:
        def append_audit(self, _record: AuditRecord) -> None:
            raise RuntimeError("secret-payload")

        def query_audit(self, _filter_by=None):
            return []

        def close(self) -> None:
            return None

    runtime = A2ARuntime(
        state_store=MemoryStateStore(),
        audit_store=_FailingAuditStore(),  # type: ignore[arg-type]
    )
    runtime.register_agent("echo", ["echo."], lambda _envelope: {"ok": True})
    envelope = Envelope.new(
        from_agent="tester",
        to_agent="echo",
        to_capability=None,
        type="call",
        method="echo.ping",
        params={"secret": "not logged"},
        idempotency_key="audit-warning",
    )
    try:
        response = runtime.call(envelope)
    finally:
        runtime.close()

    assert response.params["ok"] is True
    assert "A2A audit append failed" in caplog.text
    assert "secret-payload" not in caplog.text
    assert "not logged" not in caplog.text
