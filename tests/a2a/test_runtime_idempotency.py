from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.a2a.artifacts import LocalArtifactStore
from openminion.modules.a2a.models import Envelope
from openminion.modules.a2a.runtime import A2ARuntime
from openminion.modules.a2a.storage import MemoryAuditStore, SQLiteStateStore


@pytest.fixture
def runtime(tmp_path: Path) -> A2ARuntime:
    runtime = A2ARuntime(
        state_store=SQLiteStateStore(tmp_path / "state.db"),
        audit_store=MemoryAuditStore(),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        max_inline_bytes=128,
    )
    try:
        yield runtime
    finally:
        runtime.close()


def test_duplicate_call_is_served_from_idempotency_cache(runtime: A2ARuntime) -> None:
    calls = {"count": 0}

    def handler(envelope: Envelope) -> dict:
        calls["count"] += 1
        return {
            "sum": int(envelope.params.get("a", 0)) + int(envelope.params.get("b", 0))
        }

    runtime.register_agent("calc", ["math."], handler)

    req = Envelope.new(
        from_agent="tester",
        to_agent="calc",
        to_capability=None,
        type="call",
        method="math.add",
        params={"a": 2, "b": 3},
        idempotency_key="call-1",
        timeout_ms=1000,
    )

    first = runtime.call(req)
    second = runtime.call(req)

    assert calls["count"] == 1
    assert first.params["ok"] is True
    assert second.params["ok"] is True
    assert first.params["data"] == {"sum": 5}
    assert second.params["data"] == {"sum": 5}
    assert first.params["cached"] is False
    assert second.params["cached"] is True
