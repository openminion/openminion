from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from openminion.modules.a2a.artifacts import LocalArtifactStore
from openminion.modules.a2a.errors import (
    A2AError,
    ERROR_CODE_CANCELED,
    ERROR_CODE_HANDLER_ERROR,
    ERROR_CODE_INTERNAL_ERROR,
)
from openminion.modules.a2a.models import Envelope
from openminion.modules.a2a.runtime import A2ARuntime
from openminion.modules.a2a.storage import MemoryAuditStore, SQLiteStateStore


@pytest.fixture
def runtime(tmp_path: Path):
    state = SQLiteStateStore(tmp_path / "state.db")
    audit = MemoryAuditStore()
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    runtime = A2ARuntime(
        state_store=state,
        audit_store=audit,
        artifact_store=artifacts,
        max_inline_bytes=512,
    )
    try:
        yield runtime
    finally:
        runtime.close()


def test_unknown_error_code_normalizes_to_internal() -> None:
    err = A2AError("some_unknown_code", "boom")
    assert err.code == ERROR_CODE_INTERNAL_ERROR


def test_runtime_call_wraps_unexpected_handler_error(runtime: A2ARuntime) -> None:
    def handler(_: Envelope) -> dict:
        raise RuntimeError("boom")

    runtime.register_agent("err", ["fail."], handler)
    req = Envelope.new(
        from_agent="tester",
        to_agent="err",
        to_capability=None,
        type="call",
        method="fail.now",
        params={},
        idempotency_key="call-error",
        timeout_ms=1000,
    )

    resp = runtime.call(req)
    assert resp.params["ok"] is False
    assert resp.params["status"] == ERROR_CODE_HANDLER_ERROR
    assert resp.params["error"]["code"] == ERROR_CODE_HANDLER_ERROR


def test_job_cancel_returns_standard_code(runtime: A2ARuntime) -> None:
    unblock = threading.Event()

    def handler(_: Envelope) -> dict:
        unblock.wait(1.0)
        return {"done": True}

    runtime.register_agent("worker", ["job."], handler)
    req = Envelope.new(
        from_agent="tester",
        to_agent="worker",
        to_capability=None,
        type="job.start",
        method="job.long",
        params={},
        idempotency_key="job-cancel",
        timeout_ms=2000,
    )

    task_id = runtime.job_start(req)
    time.sleep(0.05)
    job_row = runtime.job_cancel(task_id)
    unblock.set()

    assert job_row.state == "CANCELED"
    assert job_row.error is not None
    assert job_row.error.get("code") == ERROR_CODE_CANCELED
