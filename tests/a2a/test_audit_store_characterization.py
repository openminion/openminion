from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from openminion.modules.a2a.models import AuditRecord
from openminion.modules.a2a.storage import SQLiteAuditStore


def _audit_record(
    *,
    ts: str,
    msg_id: str,
    trace_id: str,
    method: str,
    status: str = "SUCCESS",
    error_code: str | None = None,
) -> AuditRecord:
    return AuditRecord(
        ts=ts,
        msg_id=msg_id,
        trace_id=trace_id,
        from_agent="agent.from",
        to_agent="agent.to",
        to_capability=None,
        type="call",
        method=method,
        status=status,
        task_id=None,
        error_code=error_code,
        error_message=None if error_code is None else "boom",
        envelope={"msg_id": msg_id},
        data={"ok": error_code is None},
    )


def test_sqlite_audit_store_round_trip_trace_and_range_query(tmp_path: Path) -> None:
    store = SQLiteAuditStore(tmp_path / "audit", retention_days=14)
    older = datetime.now(timezone.utc) - timedelta(days=1, minutes=5)
    newer = older + timedelta(minutes=5)

    store.append_audit(
        _audit_record(
            ts=older.isoformat(),
            msg_id="msg-1",
            trace_id="trace-1",
            method="job.start",
        )
    )
    store.append_audit(
        _audit_record(
            ts=newer.isoformat(),
            msg_id="msg-2",
            trace_id="trace-2",
            method="job.status",
        )
    )

    trace_rows = store.query_audit({"trace_id": "trace-1", "limit": 10})
    assert [row.msg_id for row in trace_rows] == ["msg-1"]

    range_rows = store.query_audit(
        {
            "since_ts": older.isoformat(),
            "until_ts": older.isoformat(),
            "limit": 10,
        }
    )
    assert [row.msg_id for row in range_rows] == ["msg-1"]


def test_sqlite_audit_store_retention_archives_old_daily_db(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    store = SQLiteAuditStore(audit_root, retention_days=1)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    fresh_ts = datetime.now(timezone.utc).isoformat()

    store.append_audit(
        _audit_record(
            ts=old_ts,
            msg_id="old-msg",
            trace_id="trace-old",
            method="job.old",
            status="FAILED",
            error_code="OLD",
        )
    )
    store.append_audit(
        _audit_record(
            ts=fresh_ts,
            msg_id="new-msg",
            trace_id="trace-new",
            method="job.new",
        )
    )

    old_db = audit_root / f"{old_ts[:10]}.db"
    archived = audit_root / "archive" / f"{old_db.name}.gz"
    assert not old_db.exists()
    assert archived.exists()
    assert [row.msg_id for row in store.query_audit({"trace_id": "trace-new"})] == [
        "new-msg"
    ]


def test_sqlite_audit_store_close_is_noop(tmp_path: Path) -> None:
    store = SQLiteAuditStore(tmp_path / "audit", retention_days=14)
    store.close()
