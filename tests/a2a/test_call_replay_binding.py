from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from openminion.modules.a2a.errors import A2AError
from openminion.modules.a2a.models import Envelope
from openminion.modules.a2a.runtime import A2ARuntime
from openminion.modules.a2a.storage import (
    MemoryAuditStore,
    MemoryStateStore,
    SQLiteStateStore,
)

CONFLICT = "IDEMPOTENCY_CONFLICT"


@pytest.fixture(params=["memory", "sqlite"])
def runtime(request, tmp_path):
    store = (
        MemoryStateStore()
        if request.param == "memory"
        else SQLiteStateStore(tmp_path / "state.db")
    )
    runtime = A2ARuntime(state_store=store, audit_store=MemoryAuditStore())
    yield runtime
    runtime.close()


def call_request(params, *, sender="client-a", session=""):
    return Envelope.new(
        from_agent=sender,
        to_agent="agent.echo",
        to_capability=None,
        type="call",
        method="echo.run",
        params=params,
        meta={"session_id": session},
        idempotency_key="shared-key",
        timeout_ms=5000,
    )


@pytest.mark.parametrize("fails", [False, True])
def test_terminal_call_binds_arguments(runtime, fails):
    calls = []

    def handler(envelope):
        calls.append(envelope.params)
        if fails:
            raise A2AError("INVALID_ARGUMENT", "Original handler error")
        return envelope.params

    runtime.register_agent("agent.echo", ["echo."], handler)
    first = runtime.call(call_request({"payload": {"a": 1, "b": [2, 3]}}))
    with pytest.raises(A2AError, match=CONFLICT):
        runtime.call(call_request({"payload": {"a": 1, "b": [3, 2]}}))
    replay = runtime.call(call_request({"payload": {"b": [2, 3], "a": 1}}))
    assert replay.params == {**first.params, "cached": True}
    assert calls == [{"payload": {"a": 1, "b": [2, 3]}}]


def test_sender_and_session_scopes(runtime):
    runtime.register_agent("agent.echo", ["echo."], lambda envelope: envelope.params)
    for sender, session, value in [
        ("client-a", "", 1),
        ("client-b", "", 2),
        ("client-a", "session-1", 3),
        ("client-a", "session-2", 4),
        ("client-b", "session-1", 5),
    ]:
        request = call_request({"value": value}, sender=sender, session=session)
        first = runtime.call(request)
        assert first.params["data"] == {"value": value}
        assert first.params["cached"] is False
        assert runtime.call(request).params == {**first.params, "cached": True}


def test_in_progress_call_binds_arguments(runtime):
    entered = Event()
    release = Event()

    def handler(envelope):
        entered.set()
        assert release.wait(5)
        return envelope.params

    runtime.register_agent("agent.echo", ["echo."], handler)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(runtime.call, call_request({"value": 1}))
        try:
            assert entered.wait(5)
            with pytest.raises(A2AError, match=CONFLICT):
                runtime.call(call_request({"value": 2}))
            pending = runtime.call(call_request({"value": 1}))
            assert pending.params["status"] == "IN_PROGRESS"
            assert pending.params["cached"] is True
        finally:
            release.set()
        assert first.result().params["data"] == {"value": 1}


@pytest.mark.parametrize("status", ["IN_PROGRESS", "SUCCESS", "FAILED"])
@pytest.mark.parametrize("session", ["", "session-1"])
def test_legacy_call_is_neither_replayed_nor_dispatched(runtime, status, session):
    scope = "call:agent.echo:echo.run"
    if session:
        scope += f":{session}"
    store = runtime.state_store
    store.reserve_idempotency("shared-key", scope)
    if status != "IN_PROGRESS":
        store.set_idempotency_result(
            "shared-key", scope, status, result_inline={"legacy": True}
        )
    calls = []
    runtime.register_agent("agent.echo", ["echo."], lambda env: calls.append(env))
    for sender in ("client-a", "client-b"):
        with pytest.raises(A2AError, match=CONFLICT):
            runtime.call(call_request({"value": 1}, sender=sender, session=session))
    assert calls == []
    _, original = store.reserve_idempotency("shared-key", scope)
    assert original.status == status


def test_sqlite_reopen_retains_invocation(tmp_path: Path):
    def open_runtime():
        instance = A2ARuntime(
            state_store=SQLiteStateStore(tmp_path / "state.db"),
            audit_store=MemoryAuditStore(),
        )
        instance.register_agent(
            "agent.echo", ["echo."], lambda envelope: envelope.params
        )
        return instance

    first = open_runtime()
    try:
        first.call(call_request({"value": 1}))
    finally:
        first.close()
    reopened = open_runtime()
    try:
        with pytest.raises(A2AError, match=CONFLICT):
            reopened.call(call_request({"value": 2}))
        assert reopened.call(call_request({"value": 1})).params["cached"] is True
    finally:
        reopened.close()
