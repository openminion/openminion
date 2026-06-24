from __future__ import annotations

import time
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


def test_startup_recovery_marks_stale_jobs_failed(root: Path) -> None:
    state_path = root / "state-recovery.db"

    seed_state = SQLiteStateStore(state_path)
    stale_heartbeat = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
    stale_job = JobRecord(
        task_id="task-stale",
        trace_id="trace-stale",
        idempotency_key="idem-stale",
        agent_id="worker",
        method="job.run",
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
        "job.start:worker:job.run",
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
