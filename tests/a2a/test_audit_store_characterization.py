from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from stat import S_IMODE

from openminion.modules.a2a.models import AuditRecord
import pytest

from openminion.modules.a2a.storage import MemoryAuditStore, SQLiteAuditStore


_OBSERVABILITY = {
    "schema_version": "openminion.a2a_observability.v1",
    "invocation_id": "11111111-1111-4111-8111-111111111111",
    "execution_id": "21111111-1111-4111-8111-111111111111",
    "handoff_id": "31111111-1111-4111-8111-111111111111",
    "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
    "tracestate": "vendor=unbounded-caller-value",
}


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
        envelope={
            "msg_id": msg_id,
            "params": {"instruction": "sensitive task"},
            "meta": {"freeform": "metadata"},
            "observability": dict(_OBSERVABILITY),
        },
        data={"result": "sensitive result"},
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


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_audit_query_filters_before_selecting_newest_bounded_window(
    backend: str,
    tmp_path: Path,
) -> None:
    store = (
        MemoryAuditStore()
        if backend == "memory"
        else SQLiteAuditStore(tmp_path / "audit")
    )
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    records = (
        ("success-1", None),
        ("success-2", None),
        ("error-1", "FAILED_1"),
        ("error-2", "FAILED_2"),
        ("error-3", "FAILED_3"),
    )
    for offset, (msg_id, error_code) in enumerate(records):
        store.append_audit(
            _audit_record(
                ts=(start + timedelta(minutes=offset)).isoformat(),
                msg_id=msg_id,
                trace_id=f"trace-{msg_id}",
                method="job.status",
                status="FAILED" if error_code else "SUCCESS",
                error_code=error_code,
            )
        )

    assert [
        row.msg_id for row in store.query_audit({"error_only": True, "limit": 2})
    ] == ["error-2", "error-3"]
    assert [row.msg_id for row in store.query_audit({"limit": 2})] == [
        "error-2",
        "error-3",
    ]


def test_default_capture_keeps_only_structural_allowlist(tmp_path: Path) -> None:
    store = SQLiteAuditStore(tmp_path / "audit")
    record = _audit_record(
        ts=datetime.now(timezone.utc).isoformat(),
        msg_id="msg-structural",
        trace_id="trace-structural",
        method="job.failed",
        status="FAILED",
        error_code="FAILED",
    )

    store.append_audit(record)
    [stored] = store.query_audit({"trace_id": record.trace_id})

    assert stored.error_message is None
    assert stored.data is None
    expected_observability = dict(_OBSERVABILITY)
    expected_observability.pop("tracestate")
    assert stored.envelope == {"observability": expected_observability}
    assert {
        key: value
        for key, value in stored.to_dict().items()
        if key not in {"error_message", "envelope", "data"}
    } == {
        key: value
        for key, value in record.to_dict().items()
        if key not in {"error_message", "envelope", "data"}
    }


def test_capture_payloads_opt_in_round_trips_details(tmp_path: Path) -> None:
    store = SQLiteAuditStore(tmp_path / "audit", capture_payloads=True)
    record = _audit_record(
        ts=datetime.now(timezone.utc).isoformat(),
        msg_id="msg-detailed",
        trace_id="trace-detailed",
        method="job.failed",
        status="FAILED",
        error_code="FAILED",
    )

    store.append_audit(record)

    assert store.query_audit({"trace_id": record.trace_id}) == [record]


def test_sqlite_audit_store_retention_archives_old_daily_db(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    store = SQLiteAuditStore(
        audit_root,
        retention_days=1,
        archive_retention_days=30,
    )
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
    assert S_IMODE(audit_root.stat().st_mode) == 0o700
    assert S_IMODE((audit_root / "archive").stat().st_mode) == 0o700
    assert S_IMODE(archived.stat().st_mode) == 0o600


def test_sqlite_audit_database_uses_private_permissions(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    store = SQLiteAuditStore(audit_root)
    now = datetime.now(timezone.utc).isoformat()

    store.append_audit(
        _audit_record(
            ts=now,
            msg_id="private-msg",
            trace_id="private-trace",
            method="job.private",
        )
    )

    assert S_IMODE(audit_root.stat().st_mode) == 0o700
    assert S_IMODE((audit_root / f"{now[:10]}.db").stat().st_mode) == 0o600


def test_sqlite_default_retention_deletes_old_daily_db_on_append(
    tmp_path: Path,
) -> None:
    audit_root = tmp_path / "audit"
    store = SQLiteAuditStore(audit_root, retention_days=1)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()

    store.append_audit(
        _audit_record(
            ts=old_ts,
            msg_id="old-msg",
            trace_id="trace-old",
            method="job.old",
        )
    )

    assert not (audit_root / f"{old_ts[:10]}.db").exists()
    assert not (audit_root / "archive" / f"{old_ts[:10]}.db.gz").exists()


def test_sqlite_retention_runs_on_construction_and_query(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    old_ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    original = SQLiteAuditStore(audit_root, retention_days=36_500)
    original.append_audit(
        _audit_record(
            ts=old_ts,
            msg_id="old-msg",
            trace_id="trace-old",
            method="job.old",
        )
    )
    original.close()

    store = SQLiteAuditStore(audit_root, retention_days=1)
    assert not (audit_root / f"{old_ts[:10]}.db").exists()

    stale_day = (datetime.now(timezone.utc) - timedelta(days=4)).date().isoformat()
    stale_path = audit_root / f"{stale_day}.db"
    stale_path.touch()
    assert store.query_audit() == []
    assert not stale_path.exists()


def test_sqlite_retention_removes_expired_wal_and_shm_sidecars(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    audit_root.mkdir()
    stale_day = (datetime.now(timezone.utc) - timedelta(days=4)).date().isoformat()
    db_path = audit_root / f"{stale_day}.db"
    wal_path = audit_root / f"{stale_day}.db-wal"
    shm_path = audit_root / f"{stale_day}.db-shm"
    for path in (db_path, wal_path, shm_path):
        path.touch()

    SQLiteAuditStore(audit_root, retention_days=1)

    assert not db_path.exists()
    assert not wal_path.exists()
    assert not shm_path.exists()


def test_sqlite_retention_keeps_exact_cutoff_day(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    expired_ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    seed = SQLiteAuditStore(audit_root, retention_days=36_500)
    for ts, suffix in ((cutoff_ts, "cutoff"), (expired_ts, "expired")):
        seed.append_audit(
            _audit_record(
                ts=ts,
                msg_id=f"msg-{suffix}",
                trace_id=f"trace-{suffix}",
                method=f"job.{suffix}",
            )
        )

    retained = SQLiteAuditStore(audit_root, retention_days=1)

    assert [row.msg_id for row in retained.query_audit()] == ["msg-cutoff"]


def test_sqlite_archive_retention_is_total_record_age(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    store = SQLiteAuditStore(
        audit_root,
        retention_days=1,
        archive_retention_days=10,
    )
    retained_ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    expired_ts = (datetime.now(timezone.utc) - timedelta(days=12)).isoformat()

    for ts, suffix in ((retained_ts, "retained"), (expired_ts, "expired")):
        store.append_audit(
            _audit_record(
                ts=ts,
                msg_id=f"msg-{suffix}",
                trace_id=f"trace-{suffix}",
                method=f"job.{suffix}",
            )
        )

    assert (audit_root / "archive" / f"{retained_ts[:10]}.db.gz").exists()
    assert not (audit_root / "archive" / f"{expired_ts[:10]}.db.gz").exists()


def test_sqlite_audit_store_close_is_noop(tmp_path: Path) -> None:
    store = SQLiteAuditStore(tmp_path / "audit", retention_days=14)
    store.close()
