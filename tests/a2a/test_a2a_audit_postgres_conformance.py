from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from openminion.modules.a2a.models import AuditRecord
from openminion.modules.a2a.storage import build_a2a_audit_store
from openminion.modules.a2a.storage.audit_store import (
    PostgresAuditStore,
    SQLiteAuditStore,
)
from openminion.modules.storage.engine import StorageEngineConfig
from tests.storage.postgres_test_utils import (
    build_postgres_storage_config,
    open_postgres_record_store,
)


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


def _backend_params():
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", marks=pytest.mark.postgres, id="postgres"),
    ]


@pytest.fixture(params=_backend_params())
def a2a_audit_store_case(request: pytest.FixtureRequest, tmp_path: Path):
    backend = str(request.param)
    with ExitStack() as stack:
        if backend == "sqlite":
            store = build_a2a_audit_store(
                config=StorageEngineConfig(
                    root_dir=tmp_path / "storage",
                    sqlite_path=tmp_path / "audit.db",
                    fallback_root=tmp_path,
                    record_backend="record.sqlite",
                ),
                audit_root=tmp_path / "audit",
                retention_days=14,
            )
        else:
            _record_store, schema_name = stack.enter_context(
                open_postgres_record_store("sfc_a2a_audit")
            )
            del _record_store
            store = build_a2a_audit_store(
                config=build_postgres_storage_config(
                    tmp_path=tmp_path,
                    schema_name=schema_name,
                    sqlite_name="audit.db",
                ),
                audit_root=tmp_path / "audit",
                retention_days=14,
            )
        stack.callback(store.close)
        yield backend, store


def test_a2a_audit_store_round_trip(a2a_audit_store_case) -> None:
    _backend, store = a2a_audit_store_case
    earlier = datetime.now(timezone.utc) - timedelta(minutes=5)
    later = earlier + timedelta(minutes=5)

    store.append_audit(
        _audit_record(
            ts=earlier.isoformat(),
            msg_id="msg-1",
            trace_id="trace-1",
            method="job.start",
            status="FAILED",
            error_code="FAILED",
        )
    )
    store.append_audit(
        _audit_record(
            ts=later.isoformat(),
            msg_id="msg-2",
            trace_id="trace-2",
            method="job.status",
        )
    )

    assert [row.msg_id for row in store.query_audit({"trace_id": "trace-1"})] == [
        "msg-1"
    ]
    assert [row.msg_id for row in store.query_audit({"error_only": True})] == ["msg-1"]
    range_rows = store.query_audit(
        {
            "since_ts": earlier.isoformat(),
            "until_ts": later.isoformat(),
            "limit": 10,
        }
    )
    assert [row.msg_id for row in range_rows] == ["msg-1", "msg-2"]


@pytest.mark.postgres
def test_a2a_audit_store_postgres_retention_and_migration(tmp_path: Path) -> None:
    with open_postgres_record_store("sfc_a2a_audit_retention") as (
        _record_store,
        schema_name,
    ):
        del _record_store
        store = build_a2a_audit_store(
            config=build_postgres_storage_config(
                tmp_path=tmp_path,
                schema_name=schema_name,
                sqlite_name="audit.db",
            ),
            audit_root=tmp_path / "audit",
            retention_days=1,
        )
        try:
            assert isinstance(store, PostgresAuditStore)
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
            assert [row.msg_id for row in store.query_audit({"limit": 10})] == [
                "new-msg"
            ]
        finally:
            store.close()


def test_a2a_audit_store_factory_returns_sqlite_store(tmp_path: Path) -> None:
    store = build_a2a_audit_store(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "audit.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        audit_root=tmp_path / "audit",
    )
    try:
        assert isinstance(store, SQLiteAuditStore)
    finally:
        store.close()
