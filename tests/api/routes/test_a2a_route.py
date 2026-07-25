from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.client import HTTPConnection
from pathlib import Path

from openminion.api.routes import a2a
from openminion.api.routes.contracts import APIRouteContext
from openminion.api.server.app import (
    _OpenMinionAPIHandler,
    _OpenMinionThreadingHTTPServer,
)
from openminion.api.config import APIRuntimeBootstrap, build_api_handler_class
from openminion.modules.a2a.wire.google_a2a_v1.agent_card import (
    AGENT_CARD_WELL_KNOWN_PATH,
)


@dataclass
class _Runtime:
    storage_path: Path | None = None
    turns: list[dict[str, object]] = field(default_factory=list)

    def run_turn(self, *, payload: dict[str, object], request_id: str | None = None):
        self.turns.append({"payload": dict(payload), "request_id": request_id})
        return {
            "ok": True,
            "turn": {
                "trace_id": request_id,
                "final_text": f"ran:{payload.get('message')}",
                "agent_id": payload.get("agent_id", "default"),
                "session_id": payload.get("session_id", ""),
            },
        }

    def close(self) -> None:
        return None


def _ctx(
    runtime: _Runtime | None = None, token: str | None = "secret"
) -> APIRouteContext:
    headers = None if token is None else {"Authorization": f"Bearer {token}"}
    return APIRouteContext(
        config_path=None,
        runtime=runtime or _Runtime(),
        runtime_bootstrap_error=None,
        request_headers=headers,
        request_id="req-a2a",
    )


def _jsonrpc_body(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": "req-1", "method": method, "params": params}


def _wait_for_task(ctx: APIRouteContext, task_id: str) -> dict:
    for _ in range(50):
        status = a2a.handle_request(
            ctx,
            method_name="POST",
            path="/a2a/v1/jsonrpc",
            body=_jsonrpc_body("tasks/get", {"id": task_id}),
            query=None,
        )
        assert status is not None
        task = status.payload["result"]["task"]
        if task["state"] in {"completed", "failed", "canceled"}:
            return task
        time.sleep(0.02)
    raise AssertionError(f"A2A task did not finish: {task_id}")


def test_agent_card_is_public_metadata_with_bearer_auth_posture() -> None:
    result = a2a.handle_request(
        _ctx(token=None),
        method_name="GET",
        path=AGENT_CARD_WELL_KNOWN_PATH,
        body=None,
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.OK
    card = result.payload["agentCard"]
    assert card["url"] == "/a2a/v1/jsonrpc"
    assert card["capabilities"]["streaming"] is False
    assert result.payload["auth"] == {"type": "bearer", "required": True}
    assert "secret" not in json.dumps(result.payload).lower()


def test_jsonrpc_requires_configured_token(monkeypatch) -> None:
    monkeypatch.delenv(a2a.A2A_NETWORK_TOKEN_ENV, raising=False)

    result = a2a.handle_request(
        _ctx(),
        method_name="POST",
        path="/a2a/v1/jsonrpc",
        body=_jsonrpc_body("tasks/get", {"id": "missing"}),
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert result.payload["error"]["code"] == "a2a_auth_not_configured"
    assert a2a.A2A_NETWORK_TOKEN_ENV in result.payload["error"]["details"]["token_env"]


def test_jsonrpc_rejects_bad_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv(a2a.A2A_NETWORK_TOKEN_ENV, "secret")

    result = a2a.handle_request(
        _ctx(token="wrong"),
        method_name="POST",
        path="/a2a/v1/jsonrpc",
        body=_jsonrpc_body("tasks/get", {"id": "missing"}),
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.UNAUTHORIZED
    assert result.payload["error"]["code"] == "a2a_auth_required"


def test_jsonrpc_submit_status_and_cancel_use_cached_runtime(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv(a2a.A2A_NETWORK_TOKEN_ENV, "secret")
    runtime = _Runtime(storage_path=tmp_path / "runtime.sqlite")
    ctx = _ctx(runtime)

    submitted = a2a.handle_request(
        ctx,
        method_name="POST",
        path="/a2a/v1/jsonrpc",
        body=_jsonrpc_body(
            "tasks/send",
            {
                "idempotencyKey": "idem-1",
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "hello"}],
                },
            },
        ),
        query=None,
    )
    assert submitted is not None
    assert submitted.status == HTTPStatus.OK
    task_id = submitted.payload["result"]["task"]["id"]
    assert task_id
    assert getattr(runtime, "_external_a2a_runtime") is not None

    completed = _wait_for_task(ctx, task_id)
    assert completed["id"] == task_id
    assert completed["state"] == "completed"
    assert completed["metadata"]["agentId"] == "openminion.local"
    assert runtime.turns
    payload = runtime.turns[0]["payload"]
    assert payload["message"] == "hello"
    assert payload["channel"] == "a2a"
    assert payload["target"] == "external.peer"
    assert payload["inbound_metadata"]["a2a_external"] == "true"
    assert "agent_id" not in payload
    result_data = completed["messages"][0]["parts"][0]["data"]
    assert result_data["turn"]["turn"]["final_text"] == "ran:hello"

    canceled = a2a.handle_request(
        ctx,
        method_name="POST",
        path="/a2a/v1/jsonrpc",
        body=_jsonrpc_body("tasks/cancel", {"id": task_id}),
        query=None,
    )
    assert canceled is not None
    assert canceled.status == HTTPStatus.OK
    assert canceled.payload["result"]["task"]["id"] == task_id


def test_unknown_task_returns_typed_jsonrpc_error(monkeypatch) -> None:
    monkeypatch.setenv(a2a.A2A_NETWORK_TOKEN_ENV, "secret")

    result = a2a.handle_request(
        _ctx(),
        method_name="POST",
        path="/a2a/v1/jsonrpc",
        body=_jsonrpc_body("tasks/get", {"id": "missing"}),
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.OK
    assert result.payload["error"]["code"] == -32001
    assert result.payload["error"]["data"]["code"] == "JOB_NOT_FOUND"


def test_external_a2a_runtime_close_shuts_down_cached_runtime(monkeypatch) -> None:
    monkeypatch.setenv(a2a.A2A_NETWORK_TOKEN_ENV, "secret")
    runtime = _Runtime()
    ctx = _ctx(runtime)

    submitted = a2a.handle_request(
        ctx,
        method_name="POST",
        path="/a2a/v1/jsonrpc",
        body=_jsonrpc_body(
            "tasks/send",
            {"idempotencyKey": "idem-close", "message": {"text": "close me"}},
        ),
        query=None,
    )
    assert submitted is not None
    assert getattr(runtime, "_external_a2a_runtime") is not None

    from openminion.modules.a2a.endpoint import close_external_a2a_runtime

    close_external_a2a_runtime(runtime)

    assert not hasattr(runtime, "_external_a2a_runtime")


def test_task_events_route_is_explicitly_not_supported(monkeypatch) -> None:
    monkeypatch.setenv(a2a.A2A_NETWORK_TOKEN_ENV, "secret")

    result = a2a.handle_request(
        _ctx(),
        method_name="GET",
        path="/a2a/v1/tasks/task-1/events",
        body=None,
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.NOT_IMPLEMENTED
    assert result.payload["error"]["code"] == "a2a_streaming_not_supported"


def test_local_http_client_smoke_for_agent_card_and_jsonrpc(monkeypatch) -> None:
    monkeypatch.setenv(a2a.A2A_NETWORK_TOKEN_ENV, "secret")
    runtime = _Runtime()
    handler_cls = build_api_handler_class(
        _OpenMinionAPIHandler,
        config_path=None,
        bootstrap=APIRuntimeBootstrap(runtime=runtime, runtime_bootstrap_error=None),
        class_name="A2ATestHandler",
    )
    server = _OpenMinionThreadingHTTPServer(("127.0.0.1", 0), handler_cls, runtime=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        conn = HTTPConnection(host, port, timeout=5)
        conn.request("GET", AGENT_CARD_WELL_KNOWN_PATH)
        card_response = conn.getresponse()
        card_payload = json.loads(card_response.read().decode("utf-8"))
        assert card_response.status == 200
        assert card_payload["agentCard"]["url"] == "/a2a/v1/jsonrpc"

        body = json.dumps(
            _jsonrpc_body(
                "tasks/send",
                {"idempotencyKey": "idem-http", "message": {"role": "user"}},
            )
        )
        conn.request(
            "POST",
            "/a2a/v1/jsonrpc",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret",
            },
        )
        submit_response = conn.getresponse()
        submit_payload = json.loads(submit_response.read().decode("utf-8"))
        assert submit_response.status == 200
        assert submit_payload["result"]["task"]["id"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
