from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.a2a.artifacts import LocalArtifactStore  # noqa: E402
from openminion.modules.a2a.errors import (  # noqa: E402
    A2AError,
    ERROR_CODE_IN_PROGRESS,
    ERROR_CODE_POLICY_DENIED,
)
from openminion.modules.a2a.models import Envelope  # noqa: E402
from openminion.modules.a2a.runtime import A2ARuntime  # noqa: E402
from openminion.modules.a2a.storage import MemoryAuditStore, SQLiteStateStore  # noqa: E402


@pytest.fixture
def runtime(tmp_path: Path) -> A2ARuntime:
    runtime = A2ARuntime(
        state_store=SQLiteStateStore(tmp_path / "state.db"),
        audit_store=MemoryAuditStore(),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        max_inline_bytes=1024,
    )
    try:
        yield runtime
    finally:
        runtime.close()


def test_cached_success_replay_shape(runtime: A2ARuntime) -> None:
    calls = {"count": 0}

    def handler(envelope: Envelope) -> dict:
        calls["count"] += 1
        return {"echo": envelope.params.get("value")}

    runtime.register_agent("calc", ["math."], handler)
    req = Envelope.new(
        from_agent="tester",
        to_agent="calc",
        to_capability=None,
        type="call",
        method="math.echo",
        params={"value": 42},
        idempotency_key="replay-success",
        timeout_ms=1000,
    )

    first = runtime.call(req)
    second = runtime.call(req)

    assert calls["count"] == 1
    assert first.params["ok"] is True
    assert first.params["cached"] is False
    assert second.params["cached"] is True
    assert second.params["status"] == "SUCCESS"
    assert second.params["data"] == {"echo": 42}
    assert second.meta.get("cached")


def test_cached_failure_replay_shape(runtime: A2ARuntime) -> None:
    def handler(_: Envelope) -> dict:
        raise A2AError(ERROR_CODE_POLICY_DENIED, "policy blocked")

    runtime.register_agent("calc", ["math."], handler)
    req = Envelope.new(
        from_agent="tester",
        to_agent="calc",
        to_capability=None,
        type="call",
        method="math.fail",
        params={},
        idempotency_key="replay-failure",
        timeout_ms=1000,
    )

    first = runtime.call(req)
    second = runtime.call(req)

    assert first.params["cached"] is False
    assert second.params["cached"] is True
    assert second.params["ok"] is False
    assert second.params["status"] == ERROR_CODE_POLICY_DENIED
    assert second.params["error"]["code"] == ERROR_CODE_POLICY_DENIED
    assert second.params["error"]["message"] == "policy blocked"
    assert second.meta.get("cached")


def test_in_progress_replay_shape(runtime: A2ARuntime) -> None:
    handler_called = {"ran": False}

    def handler(_: Envelope) -> dict:
        handler_called["ran"] = True
        return {"ok": True}

    runtime.register_agent("calc", ["math."], handler)
    req = Envelope.new(
        from_agent="tester",
        to_agent="calc",
        to_capability=None,
        type="call",
        method="math.pending",
        params={},
        idempotency_key="replay-progress",
        timeout_ms=1000,
    )

    route = runtime.registry.resolve(req)
    scope = runtime._idempotency_scope(req, route.descriptor.agent_id)
    runtime.state_store.set_idempotency_result(
        req.idempotency_key,
        scope,
        "IN_PROGRESS",
        task_id="task-in-flight",
    )

    response = runtime.call(req)

    assert handler_called["ran"] is False
    assert response.params["ok"] is False
    assert response.params["status"] == ERROR_CODE_IN_PROGRESS
    assert response.params["cached"] is True
    assert response.params["data"] == {"task_id": "task-in-flight"}
    assert response.params["task_id"] == "task-in-flight"
    assert response.meta.get("cached")
    assert response.meta.get("in_progress")
