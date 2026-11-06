from __future__ import annotations

import http.server
import threading
import time

import httpx
import pytest

from openminion.modules.a2a.errors import A2AError
from openminion.modules.a2a.models import Envelope
from openminion.modules.a2a.policy import PolicyRule
from openminion.modules.a2a.runtime import A2ARuntime
from openminion.modules.a2a.storage import MemoryAuditStore, MemoryStateStore
from openminion.modules.a2a.transport import (
    A2AHTTPServer,
    HTTPTransport,
    InProcTransport,
)

TOKEN = "test-secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _echo_handler(envelope: Envelope) -> dict:
    return {
        "agent": "agent.echo",
        "method": envelope.method,
        "params": envelope.params,
        "trace_id": envelope.trace_id,
    }


def _worker_handler(envelope: Envelope) -> dict:
    seconds = float(envelope.params.get("seconds", 0.0))
    if seconds > 0:
        time.sleep(min(seconds, 30.0))
    return {
        "agent": "agent.worker",
        "method": envelope.method,
        "params": envelope.params,
    }


@pytest.fixture
def runtime() -> A2ARuntime:
    runtime = A2ARuntime(
        state_store=MemoryStateStore(),
        audit_store=MemoryAuditStore(),
    )
    runtime.register_agent(
        "agent.echo", ["echo.", "debug."], _echo_handler, tags=["builtin", "debug"]
    )
    runtime.register_agent(
        "agent.worker",
        ["job.", "sleep.", "task."],
        _worker_handler,
        tags=["builtin", "worker"],
    )
    yield runtime
    runtime.close()


@pytest.fixture
def server(runtime: A2ARuntime) -> A2AHTTPServer:
    server = A2AHTTPServer(("127.0.0.1", 0), runtime=runtime, token=TOKEN)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def base_url(server: A2AHTTPServer) -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}"


def _envelope(
    *,
    to_agent: str,
    method: str,
    type_: str = "call",
    idempotency_key: str,
    params: dict | None = None,
) -> Envelope:
    return Envelope.new(
        from_agent="client",
        to_agent=to_agent,
        to_capability=None,
        type=type_,
        method=method,
        params=params or {},
        idempotency_key=idempotency_key,
        timeout_ms=5_000,
    )


def _invoke_op(transport: HTTPTransport, op: str) -> object:
    envelope = _envelope(
        to_agent="agent.echo",
        method="echo.hello",
        idempotency_key=f"malformed-{op}",
    )
    if op == "call":
        return transport.call(envelope)
    if op == "job_start":
        return transport.job_start(envelope)
    if op == "job_status":
        return transport.job_status("missing-task")
    if op == "job_cancel":
        return transport.job_cancel("missing-task")
    raise AssertionError(f"unknown op {op}")


def _serve_raw_response(
    status: int, body: bytes
) -> tuple[http.server.HTTPServer, threading.Thread]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            del format, args

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_round_trip_call_via_http_transport(base_url: str) -> None:
    transport = HTTPTransport(base_url, TOKEN)
    result = transport.call(
        _envelope(
            to_agent="agent.echo",
            method="echo.hello",
            idempotency_key="rt-1",
            params={"text": "hi"},
        )
    )
    assert result.type == "result"
    assert result.from_agent == "agent.echo"
    assert result.params["ok"] is True
    assert result.params["data"]["agent"] == "agent.echo"
    assert result.params["data"]["method"] == "echo.hello"
    assert result.params["data"]["params"] == {"text": "hi"}


def test_missing_token_returns_401(base_url: str) -> None:
    response = httpx.post(f"{base_url}/a2a/call", json={})
    assert response.status_code == 401


def test_wrong_token_returns_401(base_url: str) -> None:
    response = httpx.post(
        f"{base_url}/a2a/call",
        json={},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


def test_unknown_agent_returns_404(base_url: str) -> None:
    envelope = _envelope(
        to_agent="agent.missing",
        method="echo.hello",
        idempotency_key="missing-1",
    )
    response = httpx.post(f"{base_url}/a2a/call", json=envelope.to_dict(), headers=AUTH)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AGENT_NOT_FOUND"


def test_invalid_envelope_returns_400(base_url: str) -> None:
    response = httpx.post(
        f"{base_url}/a2a/call", json={"not": "an envelope"}, headers=AUTH
    )
    assert response.status_code == 400


def test_agent_card_endpoint(base_url: str) -> None:
    unauthorized = httpx.get(f"{base_url}/.well-known/agent.json")
    assert unauthorized.status_code == 401

    response = httpx.get(f"{base_url}/.well-known/agent.json", headers=AUTH)
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "openminion-a2a"
    assert payload["protocolVersion"] == "1.0"
    assert "agents" in payload
    agent_ids = {item["agent_id"] for item in payload["agents"]}
    assert "agent.echo" in agent_ids
    assert "agent.worker" in agent_ids


def test_job_round_trip_via_http_transport(base_url: str) -> None:
    transport = HTTPTransport(base_url, TOKEN)

    task_id = transport.job_start(
        _envelope(
            to_agent="agent.worker",
            method="job.run",
            type_="job.start",
            idempotency_key="job-rt-1",
            params={"seconds": 0.0},
        )
    )
    assert task_id

    deadline = time.time() + 5.0
    record = None
    while time.time() < deadline:
        record = transport.job_status(task_id)
        if record.state in {"SUCCESS", "FAILED", "CANCELED"}:
            break
        time.sleep(0.02)

    assert record is not None
    assert record.state == "SUCCESS"
    assert record.task_id == task_id
    assert record.result_inline is not None
    assert record.result_inline["agent"] == "agent.worker"

    cancel_id = transport.job_start(
        _envelope(
            to_agent="agent.worker",
            method="job.run",
            type_="job.start",
            idempotency_key="job-rt-2",
            params={"seconds": 1.0},
        )
    )
    assert cancel_id

    canceled = transport.job_cancel(cancel_id)
    assert canceled.state == "CANCELED"
    assert canceled.task_id == cancel_id


def test_direct_constructor_rejects_empty_token(runtime: A2ARuntime) -> None:
    for empty in ("", "   "):
        with pytest.raises(ValueError):
            A2AHTTPServer(("127.0.0.1", 0), runtime=runtime, token=empty)


def _assert_error_parity(
    runtime: A2ARuntime,
    base_url: str,
    envelope: Envelope,
) -> None:
    inproc = InProcTransport(runtime)
    http = HTTPTransport(base_url, TOKEN)
    with pytest.raises(A2AError) as inproc_exc:
        inproc.call(envelope)
    with pytest.raises(A2AError) as http_exc:
        http.call(envelope)
    assert http_exc.value.code == inproc_exc.value.code
    assert http_exc.value.message == inproc_exc.value.message
    assert http_exc.value.details == inproc_exc.value.details


def test_error_parity_agent_not_found(runtime: A2ARuntime, base_url: str) -> None:
    _assert_error_parity(
        runtime,
        base_url,
        _envelope(
            to_agent="agent.missing",
            method="echo.hello",
            idempotency_key="parity-agent-not-found",
        ),
    )


def test_error_parity_invalid_argument(runtime: A2ARuntime, base_url: str) -> None:
    _assert_error_parity(
        runtime,
        base_url,
        _envelope(
            to_agent="agent.echo",
            method="echo.hello",
            type_="job.start",
            idempotency_key="parity-invalid-arg",
        ),
    )


def test_error_parity_policy_denied(runtime: A2ARuntime, base_url: str) -> None:
    runtime.policy.rules.append(PolicyRule(action="deny", method_prefix="blocked."))
    _assert_error_parity(
        runtime,
        base_url,
        _envelope(
            to_agent="agent.echo",
            method="blocked.hello",
            idempotency_key="parity-policy-denied",
        ),
    )


def test_error_parity_job_not_found(runtime: A2ARuntime, base_url: str) -> None:
    inproc = InProcTransport(runtime)
    http = HTTPTransport(base_url, TOKEN)
    task_id = "definitely-missing-job-id"
    with pytest.raises(A2AError) as inproc_exc:
        inproc.job_status(task_id)
    with pytest.raises(A2AError) as http_exc:
        http.job_status(task_id)
    assert http_exc.value.code == inproc_exc.value.code
    assert http_exc.value.message == inproc_exc.value.message
    assert http_exc.value.details == inproc_exc.value.details


def test_error_from_response_preserves_envelope() -> None:
    transport = HTTPTransport("http://example.invalid", TOKEN)
    response = httpx.Response(
        404,
        json={
            "error": {
                "code": "AGENT_NOT_FOUND",
                "message": "Target agent 'agent.missing' not found",
                "details": {"agent": "agent.missing"},
            }
        },
    )
    error = transport._error_from_response(response, "/a2a/call")
    assert error.code == "AGENT_NOT_FOUND"
    assert error.message == "Target agent 'agent.missing' not found"
    assert error.details == {"agent": "agent.missing"}


def test_error_from_response_normalizes_unknown_code() -> None:
    transport = HTTPTransport("http://example.invalid", TOKEN)
    response = httpx.Response(
        400,
        json={"error": {"code": "NOT_A_REAL_CODE", "message": "boom"}},
    )
    error = transport._error_from_response(response, "/a2a/call")
    assert error.code == "INTERNAL_ERROR"
    assert error.message == "boom"


def test_error_from_response_falls_back_for_non_envelope_500() -> None:
    transport = HTTPTransport("http://example.invalid", TOKEN)
    response = httpx.Response(500, text="Internal Server Error")
    error = transport._error_from_response(response, "/a2a/call")
    assert error.code == "INTERNAL_ERROR"
    assert "HTTP 500" in error.message


@pytest.mark.parametrize("op", ["call", "job_start", "job_status", "job_cancel"])
def test_http_200_non_dict_body_raises_internal_error(op: str) -> None:
    server, thread = _serve_raw_response(200, b"[]")
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        transport = HTTPTransport(base_url, TOKEN)
        with pytest.raises(A2AError) as exc:
            _invoke_op(transport, op)
        assert exc.value.code == "INTERNAL_ERROR"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_200_bad_progress_raises_internal_error_for_job_ops() -> None:
    server, thread = _serve_raw_response(200, b'{"progress": "not-a-number"}')
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        transport = HTTPTransport(base_url, TOKEN)
        for op in ("job_status", "job_cancel"):
            with pytest.raises(A2AError) as exc:
                _invoke_op(transport, op)
            assert exc.value.code == "INTERNAL_ERROR"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
