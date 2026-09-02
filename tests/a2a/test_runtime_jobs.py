from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from openminion.modules.a2a.artifacts import LocalArtifactStore
from openminion.modules.a2a.models import Envelope, JobRecord
from openminion.modules.a2a.runtime import A2ARuntime
from openminion.modules.a2a.storage import MemoryAuditStore, SQLiteStateStore


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


def test_job_lifecycle_and_idempotent_start(root: Path) -> None:
    runtime = A2ARuntime(
        state_store=SQLiteStateStore(root / "state.db"),
        audit_store=MemoryAuditStore(),
        artifact_store=LocalArtifactStore(root / "artifacts"),
        recovery_stale_heartbeat_sec=60,
    )
    try:

        def handler(envelope: Envelope) -> dict:
            sleep = float(envelope.params.get("seconds", 0.0))
            if sleep > 0:
                time.sleep(sleep)
            return {"ok": True, "method": envelope.method}

        runtime.register_agent("worker", ["job."], handler)

        req = Envelope.new(
            from_agent="tester",
            to_agent="worker",
            to_capability=None,
            type="job.start",
            method="job.run",
            params={"seconds": 0.05},
            idempotency_key="job-1",
            timeout_ms=5000,
        )

        task_id = runtime.job_start(req)
        assert task_id

        duplicate = runtime.job_start(req)
        assert duplicate == task_id

        deadline = time.time() + 5.0
        last = None
        while time.time() < deadline:
            last = runtime.job_status(task_id)
            if last.state in {"SUCCESS", "FAILED", "CANCELED"}:
                break
            time.sleep(0.02)

        assert last is not None
        assert last.state == "SUCCESS"
        assert last.result_inline == {"ok": True, "method": "job.run"}
    finally:
        runtime.close()


def test_concurrent_idempotent_starts_return_one_task(root: Path) -> None:
    runtime = A2ARuntime(
        state_store=SQLiteStateStore(root / "state-concurrent-start.db"),
        audit_store=MemoryAuditStore(),
        recovery_stale_heartbeat_sec=60,
    )
    worker_count = 32
    start_barrier = threading.Barrier(worker_count)
    release = threading.Event()
    runtime.register_agent(
        "worker",
        ["job."],
        lambda _envelope: (release.wait(2.0), {"ok": True})[1],
    )
    request = Envelope.new(
        from_agent="parent",
        to_agent="worker",
        to_capability=None,
        type="job.start",
        method="job.run",
        params={},
        idempotency_key="same-concurrent-job",
        timeout_ms=5000,
    )

    def start_job(_: int) -> str:
        start_barrier.wait()
        return runtime.job_start(request)

    try:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            task_ids = list(executor.map(start_job, range(worker_count)))

        assert len(set(task_ids)) == 1
    finally:
        release.set()
        runtime.close(wait=True)


def test_cancel_updates_session_scoped_idempotency_record(root: Path) -> None:
    state_path = root / "state-cancel.db"
    runtime = A2ARuntime(
        state_store=SQLiteStateStore(state_path),
        audit_store=MemoryAuditStore(),
        recovery_stale_heartbeat_sec=60,
    )
    runtime.register_agent(
        "worker",
        ["job."],
        lambda envelope: (time.sleep(0.2), {"ok": True})[1],
    )
    request = Envelope.new(
        from_agent="parent",
        to_agent="worker",
        to_capability=None,
        type="job.start",
        method="job.run",
        params={},
        idempotency_key="job-session-cancel",
        timeout_ms=5000,
        meta={"session_id": "parent-session"},
    )
    try:
        task_id = runtime.job_start(request)
        canceled = runtime.job_cancel(task_id)
        assert canceled.state == "CANCELED"
    finally:
        runtime.close(wait=True)

    with sqlite3.connect(state_path) as connection:
        rows = connection.execute(
            "SELECT scope, status FROM idempotency_keys WHERE key = ?",
            ("job-session-cancel",),
        ).fetchall()
    assert rows == [("job.start:worker:job.run:parent-session", "CANCELED")]


def test_cancel_signals_registered_job_handler(root: Path) -> None:
    runtime = A2ARuntime(
        state_store=SQLiteStateStore(root / "state-job-handler-cancel.db"),
        audit_store=MemoryAuditStore(),
        recovery_stale_heartbeat_sec=60,
    )
    started = threading.Event()
    stopped = threading.Event()
    side_effect = threading.Event()

    def job_handler(envelope: Envelope, cancel_event: threading.Event) -> dict:
        del envelope
        started.set()
        if cancel_event.wait(2.0):
            stopped.set()
            return {"cancelled": True}
        side_effect.set()
        return {"cancelled": False}

    runtime.register_agent(
        "worker",
        ["job."],
        lambda envelope: {"sync": envelope.method},
        job_handler=job_handler,
    )
    request = Envelope.new(
        from_agent="parent",
        to_agent="worker",
        to_capability=None,
        type="job.start",
        method="job.run",
        params={},
        idempotency_key="job-handler-cancel",
        timeout_ms=5000,
    )
    try:
        task_id = runtime.job_start(request)
        assert started.wait(1.0)

        canceled = runtime.job_cancel(task_id)

        assert canceled.state == "CANCELED"
        assert stopped.wait(1.0)
        assert not side_effect.is_set()
    finally:
        runtime.close(wait=True)


def test_cancel_before_running_transition_stays_canceled(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = A2ARuntime(
        state_store=SQLiteStateStore(root / "state-cancel-before-running.db"),
        audit_store=MemoryAuditStore(),
        recovery_stale_heartbeat_sec=60,
    )
    transition_started = threading.Event()
    allow_transition = threading.Event()
    handler_called = threading.Event()
    mark_running = runtime._mark_job_running

    def delayed_mark_running(**kwargs: object) -> None:
        transition_started.set()
        assert allow_transition.wait(1.0)
        mark_running(**kwargs)

    monkeypatch.setattr(runtime, "_mark_job_running", delayed_mark_running)
    runtime.register_agent(
        "worker",
        ["job."],
        lambda envelope: (handler_called.set(), {"method": envelope.method})[1],
    )
    request = Envelope.new(
        from_agent="parent",
        to_agent="worker",
        to_capability=None,
        type="job.start",
        method="job.run",
        params={},
        idempotency_key="cancel-before-running",
        timeout_ms=5000,
    )
    try:
        task_id = runtime.job_start(request)
        assert transition_started.wait(1.0)
        future = runtime._futures[task_id]

        assert runtime.job_cancel(task_id).state == "CANCELED"
        allow_transition.set()
        future.result(timeout=1.0)

        assert runtime.job_status(task_id).state == "CANCELED"
        assert not handler_called.is_set()
    finally:
        allow_transition.set()
        runtime.close(wait=True)


def test_cancel_during_success_transition_stays_canceled(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = A2ARuntime(
        state_store=SQLiteStateStore(root / "state-cancel-during-success.db"),
        audit_store=MemoryAuditStore(),
        recovery_stale_heartbeat_sec=60,
    )
    transition_started = threading.Event()
    allow_transition = threading.Event()
    mark_success = runtime._mark_job_success

    def delayed_mark_success(**kwargs: object) -> None:
        transition_started.set()
        assert allow_transition.wait(1.0)
        mark_success(**kwargs)

    monkeypatch.setattr(runtime, "_mark_job_success", delayed_mark_success)
    runtime.register_agent("worker", ["job."], lambda envelope: {"ok": True})
    request = Envelope.new(
        from_agent="parent",
        to_agent="worker",
        to_capability=None,
        type="job.start",
        method="job.run",
        params={},
        idempotency_key="cancel-during-success",
        timeout_ms=5000,
    )
    try:
        task_id = runtime.job_start(request)
        assert transition_started.wait(1.0)
        future = runtime._futures[task_id]

        assert runtime.job_cancel(task_id).state == "CANCELED"
        allow_transition.set()
        future.result(timeout=1.0)

        assert runtime.job_status(task_id).state == "CANCELED"
    finally:
        allow_transition.set()
        runtime.close(wait=True)


def test_canceled_queued_jobs_release_runtime_bookkeeping(root: Path) -> None:
    runtime = A2ARuntime(
        state_store=SQLiteStateStore(root / "state-cancel-queued.db"),
        audit_store=MemoryAuditStore(),
        recovery_stale_heartbeat_sec=60,
        max_workers=1,
    )
    first_started = threading.Event()
    release_first = threading.Event()

    def job_handler(envelope: Envelope, cancel_event: threading.Event) -> dict:
        del cancel_event
        if envelope.params.get("block"):
            first_started.set()
            assert release_first.wait(2.0)
        return {"ok": True}

    runtime.register_agent(
        "worker",
        ["job."],
        lambda envelope: {"method": envelope.method},
        job_handler=job_handler,
    )

    def request(key: str, *, block: bool = False) -> Envelope:
        return Envelope.new(
            from_agent="parent",
            to_agent="worker",
            to_capability=None,
            type="job.start",
            method="job.run",
            params={"block": block},
            idempotency_key=key,
            timeout_ms=5000,
        )

    try:
        first_task_id = runtime.job_start(request("first-job", block=True))
        assert first_started.wait(1.0)
        queued_task_ids = [
            runtime.job_start(request(f"queued-job-{index}")) for index in range(20)
        ]

        for task_id in queued_task_ids:
            assert runtime.job_cancel(task_id).state == "CANCELED"

        release_first.set()
        deadline = time.time() + 2.0
        while time.time() < deadline:
            with runtime._lock:
                if not runtime._futures and not runtime._cancel_events:
                    break
            time.sleep(0.01)

        assert runtime.job_status(first_task_id).state == "SUCCESS"
        with runtime._lock:
            assert runtime._futures == {}
            assert runtime._cancel_events == {}
    finally:
        release_first.set()
        runtime.close(wait=True)


def test_startup_recovery_marks_stale_jobs_failed(root: Path) -> None:
    state_path = root / "state-recovery.db"

    seed_state = SQLiteStateStore(state_path)
    stale_heartbeat = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
    idempotency_scope = "job.start:worker:job.run:parent-session"
    stale_job = JobRecord(
        task_id="task-stale",
        trace_id="trace-stale",
        idempotency_key="idem-stale",
        agent_id="worker",
        method="job.run",
        idempotency_scope=idempotency_scope,
        state="RUNNING",
        current_step="executing",
        progress=0.4,
        created_at=stale_heartbeat,
        updated_at=stale_heartbeat,
        heartbeat_at=stale_heartbeat,
    )
    seed_state.create_job(stale_job)
    seed_state.set_idempotency_result(
        "idem-stale",
        idempotency_scope,
        "IN_PROGRESS",
        task_id="task-stale",
    )
    seed_state.close()

    recovered = A2ARuntime(
        state_store=SQLiteStateStore(state_path),
        audit_store=MemoryAuditStore(),
        artifact_store=LocalArtifactStore(root / "artifacts-recovery"),
        recovery_stale_heartbeat_sec=1,
    )
    try:
        row = recovered.job_status("task-stale")
        assert row.state == "FAILED"
        assert row.error is not None
        assert row.error.get("code") == "STALE_JOB"
    finally:
        recovered.close()

    with sqlite3.connect(state_path) as connection:
        rows = connection.execute(
            "SELECT scope, status FROM idempotency_keys WHERE key = ?",
            ("idem-stale",),
        ).fetchall()
    assert rows == [(idempotency_scope, "FAILED")]


def test_status_recovers_job_that_becomes_stale_after_restart(root: Path) -> None:
    state_path = root / "state-delayed-recovery.db"
    seed_state = SQLiteStateStore(state_path)
    now = datetime.now(timezone.utc).isoformat()
    job = JobRecord(
        task_id="fresh-at-restart",
        trace_id="trace-delayed-recovery",
        idempotency_key="idem-delayed-recovery",
        idempotency_scope="job.start:worker:job.run:parent-session",
        agent_id="worker",
        method="job.run",
        state="RUNNING",
        current_step="executing",
        progress=0.2,
        created_at=now,
        updated_at=now,
        heartbeat_at=now,
    )
    seed_state.create_job(job)
    seed_state.set_idempotency_result(
        job.idempotency_key,
        job.idempotency_scope,
        "IN_PROGRESS",
        task_id=job.task_id,
    )
    seed_state.close()

    restarted = A2ARuntime(
        state_store=SQLiteStateStore(state_path),
        audit_store=MemoryAuditStore(),
        recovery_stale_heartbeat_sec=1,
    )
    try:
        assert restarted.job_status(job.task_id).state == "RUNNING"
        time.sleep(1.05)

        recovered = restarted.job_status(job.task_id)

        assert recovered.state == "FAILED"
        assert recovered.error == {
            "code": "STALE_JOB",
            "message": "Job marked failed during recovery due to stale heartbeat",
        }
    finally:
        restarted.close()


def test_status_does_not_recover_job_owned_by_current_runtime(root: Path) -> None:
    runtime = A2ARuntime(
        state_store=SQLiteStateStore(root / "state-live-long-job.db"),
        audit_store=MemoryAuditStore(),
        recovery_stale_heartbeat_sec=1,
    )
    started = threading.Event()
    release = threading.Event()

    def job_handler(envelope: Envelope, cancel_event: threading.Event) -> dict:
        del envelope, cancel_event
        started.set()
        assert release.wait(2.0)
        return {"ok": True}

    runtime.register_agent(
        "worker",
        ["job."],
        lambda envelope: {"method": envelope.method},
        job_handler=job_handler,
    )
    request = Envelope.new(
        from_agent="parent",
        to_agent="worker",
        to_capability=None,
        type="job.start",
        method="job.run",
        params={},
        idempotency_key="live-long-job",
        timeout_ms=5000,
    )
    try:
        task_id = runtime.job_start(request)
        assert started.wait(1.0)
        time.sleep(1.05)

        assert runtime.job_status(task_id).state == "RUNNING"
        assert runtime.recover_stale_jobs() == []
        release.set()
        deadline = time.time() + 1.0
        while runtime.job_status(task_id).state == "RUNNING":
            assert time.time() < deadline
            time.sleep(0.01)
        assert runtime.job_status(task_id).state == "SUCCESS"
    finally:
        release.set()
        runtime.close(wait=True)


def test_completed_job_result_survives_runtime_restart(root: Path) -> None:
    state_path = root / "state-restart.db"
    first = A2ARuntime(
        state_store=SQLiteStateStore(state_path),
        audit_store=MemoryAuditStore(),
        recovery_stale_heartbeat_sec=60,
    )
    first.register_agent("worker", ["job."], lambda envelope: {"value": 7})
    request = Envelope.new(
        from_agent="parent-agent",
        to_agent="worker",
        to_capability=None,
        type="job.start",
        method="job.run",
        params={},
        idempotency_key="job-restart-1",
        timeout_ms=5000,
    )
    task_id = first.job_start(request)
    deadline = time.time() + 5
    while first.job_status(task_id).state not in {"SUCCESS", "FAILED", "CANCELED"}:
        assert time.time() < deadline
        time.sleep(0.01)
    first.close(wait=True)

    restarted = A2ARuntime(
        state_store=SQLiteStateStore(state_path),
        audit_store=MemoryAuditStore(),
        recovery_stale_heartbeat_sec=60,
    )
    try:
        recovered = restarted.job_status(task_id)
        assert recovered.state == "SUCCESS"
        assert recovered.result_inline == {"value": 7}
        assert recovered.owner_agent_id == "parent-agent"
    finally:
        restarted.close()


def test_state_store_upgrades_existing_job_columns(root: Path) -> None:
    state_path = root / "state-upgrade.db"
    with sqlite3.connect(state_path) as connection:
        connection.execute(
            """
            CREATE TABLE jobs (
                task_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                method TEXT NOT NULL,
                state TEXT NOT NULL,
                current_step TEXT NOT NULL,
                progress REAL NOT NULL,
                result_inline_json TEXT,
                result_ref TEXT,
                error_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO jobs(
                task_id, trace_id, idempotency_key, agent_id, method, state,
                current_step, progress, created_at, updated_at, heartbeat_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-task",
                "legacy-trace",
                "legacy-key",
                "worker",
                "job.run",
                "SUCCESS",
                "done",
                1.0,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )

    store = SQLiteStateStore(state_path)
    try:
        recovered = store.get_job("legacy-task")
        assert recovered is not None
        assert recovered.owner_agent_id == ""
        assert recovered.idempotency_scope == ""
    finally:
        store.close()

    with sqlite3.connect(state_path) as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(jobs)")}
    assert {"owner_agent_id", "idempotency_scope"} <= columns
