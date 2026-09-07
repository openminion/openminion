import io
import json
from pathlib import Path
from contextlib import redirect_stdout
from threading import Thread
from urllib.request import Request, urlopen

import pytest

from tests._csc_fixtures import _csc_install_default_agent

from openminion.api.server import build_api_server, dispatch_request
from openminion.base.config import OpenMinionConfig, save_config
from openminion.api.runtime import APIRuntime
from openminion.modules.task.surface import resolve_task_surface_source


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _write_echo_config(tmp_path, monkeypatch)


def test_get_session_messages_returns_transcript(config_path: Path) -> None:
    session_id = "session-api-1"
    with redirect_stdout(io.StringIO()):
        dispatch_request(
            "POST",
            "/turns",
            str(config_path),
            body={"message": "first", "session_id": session_id},
        )
        dispatch_request(
            "POST",
            "/turns",
            str(config_path),
            body={"message": "second", "session_id": session_id},
        )

    status, payload = dispatch_request(
        "GET",
        f"/sessions/{session_id}/messages",
        str(config_path),
    )
    assert int(status) == 200
    assert payload["ok"] is True
    assert payload["session"]["id"] == session_id
    assert len(payload["messages"]) == 4
    assert payload["messages"][0]["role"] == "inbound"
    assert payload["messages"][1]["role"] == "outbound"


def test_get_session_messages_limit_query(config_path: Path) -> None:
    session_id = "session-api-2"
    with redirect_stdout(io.StringIO()):
        dispatch_request(
            "POST",
            "/turns",
            str(config_path),
            body={"message": "first", "session_id": session_id},
        )

    status, payload = dispatch_request(
        "GET",
        f"/sessions/{session_id}/messages",
        str(config_path),
        query="limit=1",
    )
    assert int(status) == 200
    assert payload["ok"] is True
    assert payload["limit"] == 1
    assert len(payload["messages"]) == 1


def test_get_session_messages_not_found(config_path: Path) -> None:
    status, payload = dispatch_request(
        "GET",
        "/sessions/missing/messages",
        str(config_path),
    )
    assert int(status) == 404
    assert payload["ok"] is False
    _assert_error_envelope(payload, code="session_not_found")


def test_get_session_messages_invalid_limit(config_path: Path) -> None:
    status, payload = dispatch_request(
        "GET",
        "/sessions/any/messages",
        str(config_path),
        query="limit=bad",
    )
    assert int(status) == 400
    assert payload["ok"] is False
    _assert_error_envelope(payload, code="invalid_request")


def test_get_session_events_returns_structural_cursor_page(config_path: Path) -> None:
    session_id = "session-events-1"
    with redirect_stdout(io.StringIO()):
        dispatch_request(
            "POST",
            "/turns",
            str(config_path),
            body={"message": "record activity", "session_id": session_id},
        )
    dispatch_request(
        "POST",
        f"/sessions/{session_id}/events",
        str(config_path),
        body={"event_type": "test.secret", "payload": {"token": "sentinel-secret"}},
    )

    status, first = dispatch_request(
        "GET",
        f"/sessions/{session_id}/events",
        str(config_path),
        query="after_id=0&limit=1",
    )
    assert int(status) == 200
    assert len(first["events"]) == 1
    assert set(first["events"][0]) == {
        "id",
        "session_id",
        "event_type",
        "created_at",
        "canonical_event_id",
    }
    assert "sentinel-secret" not in json.dumps(first)

    status, remaining = dispatch_request(
        "GET",
        f"/sessions/{session_id}/events",
        str(config_path),
        query=f"after_id={first['next_after_id']}&limit=100",
    )
    assert int(status) == 200
    assert remaining["events"]
    assert all(event["id"] > first["next_after_id"] for event in remaining["events"])
    assert remaining["high_water_id"] >= remaining["next_after_id"]
    assert "sentinel-secret" not in json.dumps(remaining)

    status, beyond = dispatch_request(
        "GET",
        f"/sessions/{session_id}/events",
        str(config_path),
        query=f"after_id={remaining['high_water_id'] + 1}",
    )
    assert int(status) == 200
    assert beyond["events"] == []


@pytest.mark.parametrize("after_id", ["bad", "-1"])
def test_get_session_events_rejects_invalid_cursor(
    config_path: Path, after_id: str
) -> None:
    session_id = "session-events-invalid"
    with redirect_stdout(io.StringIO()):
        dispatch_request(
            "POST",
            "/turns",
            str(config_path),
            body={"message": "record activity", "session_id": session_id},
        )

    status, payload = dispatch_request(
        "GET",
        f"/sessions/{session_id}/events",
        str(config_path),
        query=f"after_id={after_id}",
    )

    assert int(status) == 400
    _assert_error_envelope(payload, code="invalid_request")


def test_get_session_events_distinguishes_empty_and_missing(config_path: Path) -> None:
    runtime = APIRuntime.from_config_path(str(config_path))
    runtime.sessions.resolve_session(
        agent_id="default",
        channel="api",
        target="empty",
        session_id="empty-session",
    )
    runtime.close()

    empty_status, empty = dispatch_request(
        "GET",
        "/sessions/empty-session/events",
        str(config_path),
    )
    missing_status, missing = dispatch_request(
        "GET",
        "/sessions/missing-session/events",
        str(config_path),
    )

    assert int(empty_status) == 200
    assert empty["events"] == []
    assert empty["high_water_id"] == 0
    assert int(missing_status) == 404
    _assert_error_envelope(missing, code="session_not_found")


def test_session_activity_and_artifact_metadata_survive_restart(
    config_path: Path,
) -> None:
    session_id = "session-events-restart"
    server = build_api_server(str(config_path), "127.0.0.1", 0)
    runtime = server._runtime
    assert runtime is not None
    artifact_ref = ""
    run_turn = runtime.agent.run_turn

    async def artifact_response(*args, **kwargs):
        response = await run_turn(*args, **kwargs)
        response.metadata["tool_results"] = json.dumps(
            [{"ok": True, "artifact_refs": [artifact_ref]}]
        )
        return response

    runtime.agent.run_turn = artifact_response
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        tool_status, tool = _request_json(
            f"{base_url}/v1/tools/weather/run",
            method="POST",
            body={"arguments": {"city": "Tokyo"}, "session_id": session_id},
        )
        assert tool_status == 200
        artifact_ref = tool["artifact_refs"][0]["ref"]
        _, turn = _request_json(
            f"{base_url}/turns",
            method="POST",
            body={"message": "record activity", "session_id": session_id},
        )
        task_source = resolve_task_surface_source(runtime)
        assert task_source is not None
        task_source.create_task(
            session_id=session_id,
            mode_name="research",
            goal="inspect remote work",
            agent_id="openminion",
            task_id="remote-work",
            metadata={"trace_id": tool["trace_id"]},
        )
        task_status, task_payload = _request_json(
            f"{base_url}/v1/tasks/remote-work?agent_id=openminion"
        )
        assert task_status == 200
        activity = task_payload["task"]["activity"]
        _, first = _request_json(
            f"{base_url}{activity['session_events_path']}?after_id=0&limit=1"
        )
        event_status, events = _request_json(
            f"{base_url}{activity['session_events_path']}"
            f"?after_id={first['next_after_id']}"
        )
        message_status, messages = _request_json(
            f"{base_url}{activity['session_messages_path']}"
        )
        assert turn["turn"]["session_id"] == session_id
        assert event_status == 200
        assert events["events"]
        assert message_status == 200
        assert any(
            artifact_ref in item["metadata"].get("tool_results", "")
            for item in messages["messages"]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    restarted = build_api_server(str(config_path), "127.0.0.1", 0)
    restarted_thread = Thread(target=restarted.serve_forever, daemon=True)
    restarted_thread.start()
    restarted_url = f"http://127.0.0.1:{restarted.server_address[1]}"
    try:
        event_status, events = _request_json(
            f"{restarted_url}{activity['session_events_path']}"
            f"?after_id={first['next_after_id']}"
        )
        message_status, messages = _request_json(
            f"{restarted_url}{activity['session_messages_path']}"
        )
    finally:
        restarted.shutdown()
        restarted.server_close()
        restarted_thread.join(timeout=2)

    assert event_status == 200
    assert events["events"]
    assert all(event["id"] > first["next_after_id"] for event in events["events"])
    assert message_status == 200
    assert any(
        artifact_ref in item["metadata"].get("tool_results", "")
        for item in messages["messages"]
    )


def _request_json(
    url: str,
    *,
    method: str = "GET",
    body: dict | None = None,
) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urlopen(request, timeout=5) as response:  # noqa: S310
        return response.status, json.loads(response.read().decode("utf-8"))


def _write_echo_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "api.db")
    save_config(config, str(config_path))
    return config_path


def _assert_error_envelope(payload: dict, *, code: str) -> None:
    error = payload["error"]
    assert isinstance(error, dict)
    assert error.get("code") == code
    assert "message" in error
    assert "details" in error
    assert "retryable" in error
    assert "retry_after_ms" in error
