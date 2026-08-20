from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from typing import Any

import pytest

from tests._csc_fixtures import _csc_install_default_agent

from openminion.api.runtime import APIRuntime
from openminion.api.server.client_auth import ClientAuthService, ClientIdentity
from openminion.api.server.dispatch import dispatch_request
from openminion.base.config import OpenMinionConfig, save_config


@pytest.fixture
def client_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, APIRuntime, ClientAuthService, ClientIdentity]:
    config_path = tmp_path / "config.json"
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")  # type: ignore[attr-defined]
    config.runtime.log_level = "ERROR"
    config.runtime.ipc_token = "master-token"
    config.storage.path = str(data_root / "state" / "api.db")
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    save_config(config, str(config_path))
    runtime = APIRuntime.from_config_path(str(config_path))
    service = ClientAuthService(
        master_token="master-token",
        config_path=config_path,
        home_root=home_root,
        data_root=data_root,
        bind_host="127.0.0.1",
        daemon_version="0.0.9",
    )
    lease = service.mint(protocol_min=1, protocol_max=1, ttl_seconds=60)
    identity = service.authorize(
        method="GET",
        path="/v1/client/sessions",
        master_tokens=(),
        client_tokens=(str(lease["client_token"]),),
        peer_host="127.0.0.1",
    )
    assert identity is not None
    try:
        yield config_path, runtime, service, identity
    finally:
        runtime.close()


def _request(
    client_runtime: tuple[Path, APIRuntime, ClientAuthService, ClientIdentity],
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    query: str | None = None,
    request_id: str = "desktop-request",
) -> tuple[int, dict[str, Any]]:
    config_path, runtime, service, identity = client_runtime
    status, payload = dispatch_request(
        method,
        path,
        str(config_path),
        body=body,
        query=query,
        runtime=runtime,
        client_auth=service,
        client_identity=identity,
        request_id=request_id,
    )
    return int(status), payload


def _create(
    client_runtime: tuple[Path, APIRuntime, ClientAuthService, ClientIdentity],
    **body: Any,
) -> dict[str, Any]:
    status, payload = _request(
        client_runtime,
        "POST",
        "/v1/client/sessions",
        body=body,
    )
    assert status == 200
    return payload["session"]


def test_client_session_lifecycle_uses_exact_redacted_shapes(
    client_runtime: tuple[Path, APIRuntime, ClientAuthService, ClientIdentity],
) -> None:
    session = _create(
        client_runtime,
        title="Desktop chat",
        project_id="project-1",
    )
    session_id = session["session_id"]
    assert set(session) == {
        "schema_version",
        "session_id",
        "title",
        "status",
        "created_at",
        "updated_at",
        "active_agent_id",
        "model",
        "project_id",
        "event_cursor",
    }
    assert session["title"] == "Desktop chat"
    assert session["project_id"] == "project-1"
    assert session["active_agent_id"] == "openminion"
    assert "metadata" not in session

    _, runtime, _, _ = client_runtime
    runtime.sessions.append_message(
        session_id=session_id,
        role="inbound",
        body="hello",
        metadata={"secret": "must-not-cross"},
    )
    runtime.sessions.append_message(
        session_id=session_id,
        role="outbound",
        body="hi",
        metadata={"provider_payload": "must-not-cross"},
    )
    runtime.sessions.append_message(
        session_id=session_id,
        role="system",
        body="internal",
        metadata={},
    )
    runtime.sessions.append_event(
        session_id=session_id,
        event_type="run.completed",
        payload={
            "run_id": "run-1",
            "request_id": "trace-1",
            "state": "completed",
            "provider": "echo",
            "secret": "must-not-cross",
        },
    )

    status, loaded = _request(
        client_runtime,
        "GET",
        f"/v1/client/sessions/{session_id}",
        query="message_limit=10",
    )
    assert status == 200
    assert [message["role"] for message in loaded["messages"]] == [
        "user",
        "assistant",
    ]
    assert [message["body"] for message in loaded["messages"]] == ["hello", "hi"]
    assert all(
        set(message)
        == {"schema_version", "id", "session_id", "role", "body", "created_at"}
        for message in loaded["messages"]
    )
    assert loaded["messages_has_more"] is False

    status, events = _request(
        client_runtime,
        "GET",
        f"/v1/client/sessions/{session_id}/events",
        query="limit=20",
    )
    assert status == 200
    completed = next(
        event for event in events["events"] if event["event_type"] == "run.completed"
    )
    assert completed["run_id"] == "run-1"
    assert completed["trace_id"] == "trace-1"
    assert completed["facts"] == {"provider": "echo"}
    assert "secret" not in str(events)

    status, closed = _request(
        client_runtime,
        "DELETE",
        f"/v1/client/sessions/{session_id}",
        body={"reason": "done"},
    )
    assert status == 200
    assert closed["session"]["status"] == "closed"


def test_session_and_message_pagination_use_bound_opaque_cursors(
    client_runtime: tuple[Path, APIRuntime, ClientAuthService, ClientIdentity],
) -> None:
    first = _create(client_runtime, title="first")
    second = _create(client_runtime, title="second")
    status, page_one = _request(
        client_runtime,
        "GET",
        "/v1/client/sessions",
        query="limit=1",
    )
    assert status == 200
    assert len(page_one["sessions"]) == 1
    assert page_one["has_more"] is True
    cursor = page_one["next_cursor"]
    assert "." in cursor and first["session_id"] not in cursor
    status, page_two = _request(
        client_runtime,
        "GET",
        "/v1/client/sessions",
        query=f"limit=1&cursor={cursor}",
    )
    assert status == 200
    assert (
        page_two["sessions"][0]["session_id"] != page_one["sessions"][0]["session_id"]
    )

    _, runtime, _, _ = client_runtime
    for body in ("one", "two", "three"):
        runtime.sessions.append_message(
            session_id=second["session_id"],
            role="inbound",
            body=body,
        )
    status, messages_one = _request(
        client_runtime,
        "GET",
        f"/v1/client/sessions/{second['session_id']}",
        query="message_limit=2",
    )
    assert status == 200
    assert [item["body"] for item in messages_one["messages"]] == ["one", "two"]
    assert messages_one["messages_has_more"] is True
    status, messages_two = _request(
        client_runtime,
        "GET",
        f"/v1/client/sessions/{second['session_id']}",
        query=f"message_limit=2&message_after={messages_one['next_message_cursor']}",
    )
    assert status == 200
    assert [item["body"] for item in messages_two["messages"]] == ["three"]
    assert messages_two["messages_has_more"] is False

    status, cross_session = _request(
        client_runtime,
        "GET",
        f"/v1/client/sessions/{first['session_id']}",
        query=f"message_after={messages_one['next_message_cursor']}",
    )
    assert status == 400
    assert cross_session["error"]["code"] == "invalid_cursor"

    runtime.sessions._backend.execute_count(  # type: ignore[attr-defined]
        "DELETE FROM messages WHERE id = ?",
        (messages_one["messages"][-1]["id"],),
    )
    status, expired = _request(
        client_runtime,
        "GET",
        f"/v1/client/sessions/{second['session_id']}",
        query=f"message_after={messages_one['next_message_cursor']}",
    )
    assert status == 410
    assert expired["error"]["code"] == "cursor_expired"
    assert expired["error"]["details"] == {"reload": True}

    tampered = f"{cursor[:-1]}{'A' if cursor[-1] != 'A' else 'B'}"
    status, invalid = _request(
        client_runtime,
        "GET",
        "/v1/client/sessions",
        query=f"cursor={tampered}",
    )
    assert status == 400
    assert invalid["error"]["code"] == "invalid_cursor"


@pytest.mark.parametrize(
    ("method", "path", "body", "query"),
    [
        ("GET", "/v1/client/sessions", None, "limit=0"),
        ("GET", "/v1/client/sessions", None, "unknown=1"),
        ("POST", "/v1/client/sessions", {"unknown": True}, None),
        ("POST", "/v1/client/sessions", {"title": 7}, None),
        ("POST", "/v1/client/sessions", {}, "unknown=1"),
    ],
)
def test_session_routes_reject_unknown_or_malformed_input(
    client_runtime: tuple[Path, APIRuntime, ClientAuthService, ClientIdentity],
    method: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> None:
    status, payload = _request(
        client_runtime,
        method,
        path,
        body=body,
        query=query,
    )
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"


def test_session_routes_hide_incompatible_surfaces_and_unknown_agents(
    client_runtime: tuple[Path, APIRuntime, ClientAuthService, ClientIdentity],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, runtime, _, _ = client_runtime
    runtime.sessions.resolve_session(
        agent_id="openminion",
        channel="console",
        target="tui",
        session_id="cli-session",
    )
    runtime.sessions.resolve_session(
        agent_id="openminion",
        channel="slack",
        target="api-user",
        session_id="slack-session",
    )
    status, listed = _request(client_runtime, "GET", "/v1/client/sessions")
    assert status == 200
    assert listed["sessions"] == []

    status, missing = _request(
        client_runtime,
        "GET",
        "/v1/client/sessions/cli-session",
    )
    assert status == 404
    assert missing["error"]["code"] == "session_not_found"

    runtime.sessions.append_event(
        session_id="cli-session",
        event_type="run.completed",
        payload={
            "run_id": "cli-run",
            "request_id": "cli-trace",
            "state": "completed",
        },
    )

    class CancelManager:
        calls = 0

        def cancel_session_turn(self, _trace_id: str, _session_id: str) -> str:
            self.calls += 1
            return "requested"

    manager = CancelManager()
    monkeypatch.setattr(
        "openminion.api.routes.client_sessions.resolve_runtime_manager",
        lambda **_kwargs: (manager, runtime, False),
    )
    for session_id, trace_id in (
        ("cli-session", "cli-trace"),
        ("slack-session", "active-trace"),
    ):
        status, hidden = _request(
            client_runtime,
            "POST",
            f"/v1/turn/{trace_id}/cancel",
            body={"session_id": session_id},
        )
        assert status == 404
        assert hidden["error"]["code"] == "session_not_found"
    assert manager.calls == 0

    status, invalid_agent = _request(
        client_runtime,
        "POST",
        "/v1/client/sessions",
        body={"agent_id": "missing-agent"},
    )
    assert status == 400
    assert invalid_agent["error"]["code"] == "invalid_request"


def test_terminal_cancellation_is_idempotent_and_session_bound(
    client_runtime: tuple[Path, APIRuntime, ClientAuthService, ClientIdentity],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _create(client_runtime)
    other = _create(client_runtime)
    _, runtime, _, _ = client_runtime
    runtime.sessions.append_event(
        session_id=session["session_id"],
        event_type="run.completed",
        payload={
            "run_id": "run-terminal",
            "request_id": "trace-terminal",
            "state": "completed",
        },
    )

    class CancelManager:
        calls = 0

        def cancel_session_turn(self, _trace_id: str, _session_id: str) -> str:
            self.calls += 1
            return "not_active"

    manager = CancelManager()
    monkeypatch.setattr(
        "openminion.api.routes.client_sessions.resolve_runtime_manager",
        lambda **_kwargs: (manager, runtime, False),
    )
    for _ in range(2):
        status, payload = _request(
            client_runtime,
            "POST",
            "/v1/turn/trace-terminal/cancel",
            body={"session_id": session["session_id"]},
        )
        assert status == 200
        assert payload["cancellation"] == {
            "session_id": session["session_id"],
            "trace_id": "trace-terminal",
            "run_id": "run-terminal",
            "state": "completed",
            "terminal": True,
        }
    assert manager.calls == 0

    status, unknown = _request(
        client_runtime,
        "POST",
        "/v1/turn/trace-terminal/cancel",
        body={"session_id": other["session_id"]},
    )
    assert status == 404
    assert unknown["error"]["code"] == "trace_not_found"
    assert manager.calls == 1


def test_cancellation_rechecks_terminal_state_and_serializes_duplicates(
    client_runtime: tuple[Path, APIRuntime, ClientAuthService, ClientIdentity],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = _create(client_runtime)["session_id"]
    _, runtime, _, _ = client_runtime

    class CompletionRaceManager:
        def cancel_session_turn(self, trace_id: str, supplied_session_id: str) -> str:
            runtime.sessions.append_event(
                session_id=supplied_session_id,
                event_type="run.completed",
                payload={
                    "run_id": "race-run",
                    "request_id": trace_id,
                    "state": "completed",
                },
            )
            return "requested"

    monkeypatch.setattr(
        "openminion.api.routes.client_sessions.resolve_runtime_manager",
        lambda **_kwargs: (CompletionRaceManager(), runtime, False),
    )
    status, terminal = _request(
        client_runtime,
        "POST",
        "/v1/turn/race-terminal/cancel",
        body={"session_id": session_id},
    )
    assert status == 200
    assert terminal["cancellation"]["state"] == "completed"
    assert terminal["cancellation"]["terminal"] is True

    barrier = threading.Barrier(2)

    class ConcurrentManager:
        def cancel_session_turn(self, _trace_id: str, _session_id: str) -> str:
            barrier.wait(timeout=5)
            return "requested"

    manager = ConcurrentManager()
    monkeypatch.setattr(
        "openminion.api.routes.client_sessions.resolve_runtime_manager",
        lambda **_kwargs: (manager, runtime, False),
    )

    def cancel() -> tuple[int, dict[str, Any]]:
        return _request(
            client_runtime,
            "POST",
            "/v1/turn/race-duplicate/cancel",
            body={"session_id": session_id},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: cancel(), range(2)))

    assert {status for status, _payload in results} == {200, 202}
    assert {payload["cancellation"]["state"] for _status, payload in results} == {
        "requested",
        "already_requested",
    }
    cancel_events = [
        event
        for event in runtime.sessions.list_events(session_id=session_id, limit=20)
        if event.event_type == "run.cancel_requested"
        and event.payload.get("request_id") == "race-duplicate"
    ]
    assert len(cancel_events) == 1


def test_active_duplicate_mismatch_and_stale_cancellation_are_typed(
    client_runtime: tuple[Path, APIRuntime, ClientAuthService, ClientIdentity],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _create(client_runtime)
    session_id = session["session_id"]
    _, runtime, _, _ = client_runtime

    class CancelManager:
        state = "requested"

        def cancel_session_turn(self, trace_id: str, supplied_session_id: str) -> str:
            assert trace_id in {"trace-active", "trace-failure"}
            assert supplied_session_id == session_id
            return self.state

    manager = CancelManager()
    monkeypatch.setattr(
        "openminion.api.routes.client_sessions.resolve_runtime_manager",
        lambda **_kwargs: (manager, runtime, False),
    )
    status, first = _request(
        client_runtime,
        "POST",
        "/v1/turn/trace-active/cancel",
        body={"session_id": session_id},
    )
    assert status == 202
    assert first["cancellation"]["state"] == "requested"
    manager.state = "already_requested"
    status, duplicate = _request(
        client_runtime,
        "POST",
        "/v1/turn/trace-active/cancel",
        body={"session_id": session_id},
    )
    assert status == 200
    assert duplicate["cancellation"]["state"] == "already_requested"
    assert (
        len(
            [
                event
                for event in runtime.sessions.list_events(
                    session_id=session_id, limit=20
                )
                if event.event_type == "run.cancel_requested"
            ]
        )
        == 1
    )

    manager.state = "session_mismatch"
    status, mismatch = _request(
        client_runtime,
        "POST",
        "/v1/turn/trace-active/cancel",
        body={"session_id": session_id},
    )
    assert status == 409
    assert mismatch["error"]["code"] == "trace_session_mismatch"

    runtime.sessions.append_event(
        session_id=session_id,
        event_type="run.running",
        payload={
            "run_id": "run-stale",
            "request_id": "trace-active",
            "state": "running",
        },
    )
    manager.state = "not_active"
    status, stale = _request(
        client_runtime,
        "POST",
        "/v1/turn/trace-active/cancel",
        body={"session_id": session_id},
    )
    assert status == 409
    assert stale["error"]["code"] == "stale_trace"
    assert stale["error"]["details"]["run_id"] == "run-stale"

    manager.state = "requested"

    def fail_append(**_kwargs: Any) -> None:
        raise RuntimeError("private storage detail")

    monkeypatch.setattr(runtime.sessions, "append_cancel_request_once", fail_append)
    status, failed = _request(
        client_runtime,
        "POST",
        "/v1/turn/trace-failure/cancel",
        body={"session_id": session_id},
    )
    assert status == 500
    assert failed["error"]["code"] == "cancellation_event_failed"
    assert failed["error"]["retryable"] is True
    assert "private storage detail" not in str(failed)


def test_message_and_event_bounds_stop_at_whole_items(
    client_runtime: tuple[Path, APIRuntime, ClientAuthService, ClientIdentity],
) -> None:
    session = _create(client_runtime)
    session_id = session["session_id"]
    _, runtime, _, _ = client_runtime
    runtime.sessions.append_message(
        session_id=session_id,
        role="inbound",
        body="x" * (256 * 1024 + 1),
    )
    status, message_error = _request(
        client_runtime,
        "GET",
        f"/v1/client/sessions/{session_id}",
    )
    assert status == 422
    assert message_error["error"]["code"] == "message_too_large"

    runtime.sessions.append_event(
        session_id=session_id,
        event_type="x" * (256 * 1024),
        payload={},
    )
    status, event_error = _request(
        client_runtime,
        "GET",
        f"/v1/client/sessions/{session_id}/events",
    )
    assert status == 422
    assert event_error["error"]["code"] == "event_too_large"

    page_session = _create(client_runtime)
    page_session_id = page_session["session_id"]
    facts = {
        key: "v" * (4 * 1024)
        for key in (
            "step",
            "status",
            "previous_status",
            "reason",
            "reason_code",
            "closed_at",
            "agent_id",
            "response_id",
            "provider",
            "model",
            "delivery_mode",
            "channel",
            "target",
        )
    }
    for index in range(20):
        runtime.sessions.append_event(
            session_id=page_session_id,
            event_type="run.progress",
            payload={**facts, "run_id": f"run-{index}"},
        )
    status, page = _request(
        client_runtime,
        "GET",
        f"/v1/client/sessions/{page_session_id}/events",
        query="limit=20",
    )
    assert status == 200
    assert 0 < len(page["events"]) < 20
    assert page["has_more"] is True
