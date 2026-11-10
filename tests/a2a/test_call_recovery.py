"""Fault injection against actual A2A call() and persistent reservations."""

import json
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.helpers.runtime_roots import isolate_runtime_roots

if __name__ == "__main__":
    isolate_runtime_roots(prefix="openminion-a2a-crash-")

from openminion.modules.a2a.artifacts import LocalArtifactStore  # noqa: E402
from openminion.modules.a2a.models import Envelope  # noqa: E402
from openminion.modules.a2a.runtime import A2ARuntime  # noqa: E402
from openminion.modules.a2a.storage import MemoryAuditStore, SQLiteStateStore  # noqa: E402


def request(sender="client-a", payload="A", session=None):
    return Envelope.new(
        from_agent=sender,
        to_agent="writer",
        to_capability=None,
        type="call",
        method="ticket.create",
        params={"payload": payload},
        idempotency_key="fixture-operation",
        timeout_ms=30000,
        meta={"session_id": session} if session else {},
    )


def runtime(root, handler, *, stale_after=1):
    rt = A2ARuntime(
        state_store=SQLiteStateStore(root / "runtime.db"),
        audit_store=MemoryAuditStore(),
        recovery_stale_heartbeat_sec=stale_after,
    )
    rt.register_agent("writer", ["ticket."], handler)
    return rt


def ledger(root):
    path = root / "external-ledger.jsonl"
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def effect(root, req):
    entries = ledger(root)
    entry = {
        "receipt": len(entries) + 1,
        "key": req.idempotency_key,
        "payload": req.params["payload"],
        "sender": req.from_agent,
    }
    with (root / "external-ledger.jsonl").open("a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return entry


def child(root, stage):
    def handler(req):
        if stage == "before":
            os._exit(71)
        result = effect(root, req)
        if stage == "after":
            os._exit(72)
        return result

    rt = runtime(root, handler)
    try:
        assert rt.call(request()).params["ok"]
    finally:
        rt.close()


def test_fresh_pending_call_not_dispatched(tmp_path):
    p = subprocess.run(
        [sys.executable, "-m", "tests.a2a.test_call_recovery", str(tmp_path), "after"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert p.returncode == 72, p.stderr
    rt = runtime(
        tmp_path,
        lambda req: pytest.fail("fresh pending call redispatched"),
        stale_after=300,
    )
    try:
        result = rt.call(request()).params
        assert result["status"] == "IN_PROGRESS"
        assert len(ledger(tmp_path)) == 1
    finally:
        rt.close()


def test_artifact_write_failure_after_effect_replays_unknown(tmp_path, monkeypatch):
    def fail_artifact_write(*args, **kwargs):
        raise OSError("artifact write failed")

    def handler(req):
        return effect(tmp_path, req)

    rt = runtime(tmp_path, handler)
    rt.artifact_store = LocalArtifactStore(tmp_path / "artifacts")
    rt.max_inline_bytes = 1
    monkeypatch.setattr(rt.artifact_store, "put_bytes", fail_artifact_write)
    try:
        initial = rt.call(request()).params
    finally:
        rt.close()

    reopened = runtime(tmp_path, handler)
    try:
        replay = reopened.call(request()).params
    finally:
        reopened.close()

    assert len(ledger(tmp_path)) == 1
    for result in (initial, replay):
        assert not result["ok"]
        assert result["status"] == result["error"]["code"] == "OUTCOME_UNKNOWN"
        assert "reconcile" in result["error"]["message"]
    assert not initial["cached"]
    assert replay["cached"]
    assert replay["error"] == initial["error"]


def test_receipt_write_failure_leaves_call_reserved(tmp_path, monkeypatch):
    def fail_receipt_write(*args, **kwargs):
        raise sqlite3.OperationalError("receipt write failed")

    def handler(req):
        return effect(tmp_path, req)

    rt = runtime(tmp_path, handler, stale_after=300)
    monkeypatch.setattr(rt.state_store, "finish_idempotency", fail_receipt_write)
    try:
        initial = rt.call(request()).params
        replay = rt.call(request()).params
    finally:
        rt.close()

    assert initial["status"] == initial["error"]["code"] == "OUTCOME_UNKNOWN"
    assert not initial["cached"]
    assert replay["status"] == "IN_PROGRESS"
    assert len(ledger(tmp_path)) == 1


def test_running_call_is_not_reclaimed_as_dead(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def first_handler(req):
        result = effect(tmp_path, req)
        entered.set()
        assert release.wait(10)
        return result

    first = runtime(tmp_path, first_handler)
    second = runtime(tmp_path, lambda req: effect(tmp_path, req))
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(first.call, request())
            try:
                assert entered.wait(5)
                time.sleep(1.12)
                competing = second.call(request()).params
            finally:
                release.set()
            original = future.result(timeout=5).params
        replay = second.call(request()).params
        assert len(ledger(tmp_path)) == 1
        assert competing["status"] == "OUTCOME_UNKNOWN"
        assert original["ok"] and not original["cached"]
        assert replay["ok"] and replay["cached"]
        assert original["data"] == replay["data"] == ledger(tmp_path)[0]
    finally:
        release.set()
        second.close()
        first.close()


@contextmanager
def http_endpoint(rt):
    from openminion.modules.a2a.transport import A2AHTTPServer, HTTPTransport

    token = "local-fixture-only"
    server = A2AHTTPServer(("127.0.0.1", 0), runtime=rt, token=token)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield HTTPTransport(f"http://127.0.0.1:{server.server_address[1]}", token)
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


def test_http_running_call_is_not_reclaimed(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def handler(req):
        result = effect(tmp_path, req)
        if result["receipt"] == 1:
            entered.set()
            assert release.wait(10)
        return result

    rt = runtime(tmp_path, handler)
    try:
        with http_endpoint(rt) as transport, ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(transport.call, request())
            try:
                assert entered.wait(5)
                time.sleep(1.12)
                competing = transport.call(request()).params
            finally:
                release.set()
            original = future.result(timeout=5).params
            replay = transport.call(request()).params
        assert len(ledger(tmp_path)) == 1
        assert competing["status"] == "OUTCOME_UNKNOWN"
        assert original["ok"] and not original["cached"]
        assert replay["ok"] and replay["cached"]
        assert original["data"] == replay["data"] == ledger(tmp_path)[0]
    finally:
        release.set()
        rt.close()


@pytest.mark.parametrize("over_http", [False, True], ids=["sqlite", "http"])
@pytest.mark.parametrize("stage", ["before", "after", "settled"])
def test_process_recovery(tmp_path, stage, over_http):
    # Crash without unwinding on either side of the durable external effect.
    child_process = subprocess.run(
        [sys.executable, "-m", "tests.a2a.test_call_recovery", str(tmp_path), stage],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert (
        child_process.returncode == {"before": 71, "after": 72, "settled": 0}[stage]
    ), child_process.stderr
    before = ledger(tmp_path)
    assert len(before) == (0 if stage == "before" else 1)
    time.sleep(1.12)
    rt = runtime(tmp_path, lambda req: effect(tmp_path, req))
    try:
        if over_http:
            with http_endpoint(rt) as transport:
                result = transport.call(request()).params
        else:
            result = rt.call(request()).params
        assert ledger(tmp_path) == before
        assert result["cached"]
        if stage == "settled":
            assert result["ok"] and result["data"] == before[0]
        else:
            assert not result["ok"]
            assert result["status"] == result["error"]["code"] == "OUTCOME_UNKNOWN"
    finally:
        rt.close()


@pytest.mark.parametrize("late_error", [False, True], ids=["success", "error"])
@pytest.mark.parametrize(
    "replacement_finished", [False, True], ids=["pending", "finished"]
)
def test_superseded_call_cannot_write_receipt(
    tmp_path, late_error, replacement_finished
):
    entered, release = threading.Event(), threading.Event()

    def original_handler(req):
        entered.set()
        assert release.wait(10)
        if late_error:
            raise ValueError("late error")
        return {"receipt": "late"}

    first = runtime(tmp_path, original_handler)
    # Simulate a reservation replaced by the previous runtime's reclaim algorithm.
    second = SQLiteStateStore(tmp_path / "runtime.db")
    key = request().idempotency_key
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(first.call, request())
            try:
                assert entered.wait(5)
                scope, reserved, _ = first._reserve_call(request(), "writer")
                assert not reserved
                assert (
                    second._record_store.execute_count(
                        "UPDATE idempotency_keys SET created_at = ? WHERE scope = ? AND key = ?",
                        ("2000-01-01T00:00:00+00:00", scope, key),
                    )
                    == 1
                )
                if replacement_finished:
                    second.set_idempotency_result(
                        key, scope, "SUCCESS", result_inline={"receipt": "replacement"}
                    )
            finally:
                release.set()
            late = future.result(timeout=5).params
        reserved, row = second.reserve_idempotency(key, scope)
        assert not reserved and row is not None
        if replacement_finished:
            assert row.status == "SUCCESS" and row.result_inline == {
                "receipt": "replacement"
            }
        else:
            assert (
                row.status == "IN_PROGRESS"
                and row.result_inline is None
                and row.error is None
            )
        assert late["status"] == "OUTCOME_UNKNOWN"
    finally:
        second.close()
        first.close()


if __name__ == "__main__":
    child(Path(sys.argv[1]), sys.argv[2])
