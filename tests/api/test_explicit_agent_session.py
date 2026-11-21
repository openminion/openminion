"""Explicit agent selection vs session ownership for direct and submitted turns."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from openminion import Agent, APIRuntime, ProviderError
from openminion.api.turns import TurnRequestError
from openminion.base.config import AgentProfileConfig, OpenMinionConfig, save_config
from tests._csc_fixtures import _csc_install_default_agent


@pytest.fixture
def two_agent_runtime(tmp_path, monkeypatch):
    requests: list[dict] = []
    state = {"unavailable": False}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            if state["unavailable"]:
                status = 503
                body = {"error": {"message": "local fixture unavailable"}}
            else:
                status = 200
                body = {
                    "id": "local-response",
                    "object": "chat.completion",
                    "model": request["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": f"answer from {request['model']}",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("LOCAL_SDK_KEY", "dummy-local")
    config = OpenMinionConfig()
    _csc_install_default_agent(
        config,
        name="agent-a",
        provider="openai",
        provider_config_overrides={"model": "model-a"},
    )
    config.agents["agent-b"] = AgentProfileConfig(
        name="agent-b",
        provider="openai",
        default_channel="console",
        provider_config_overrides={"model": "model-b"},
    )
    config.providers.openai.api_key_env = "LOCAL_SDK_KEY"
    config.providers.openai.base_url = f"http://127.0.0.1:{server.server_port}/v1"
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    config.storage.path = str(tmp_path / "state.db")
    config_path = tmp_path / "agents.json"
    save_config(config, str(config_path))
    runtime = APIRuntime.from_config_path(
        str(config_path), home_root=tmp_path, data_root=tmp_path / "data"
    )
    try:
        yield runtime, requests, state
    finally:
        runtime.close()
        server.shutdown()
        server.server_close()
        thread.join()


def _run(runtime, agent_id: str, message: str, session_id: str):
    agent = Agent(runtime=runtime, agent_id=agent_id, tools=[])
    return agent.run(message, session_id=session_id, timeout_seconds=30)


def _fail_first_turn(runtime, state, *, agent_id: str, session_id: str) -> None:
    # A failed first turn leaves no pending answer that could be replayed.
    state["unavailable"] = True
    try:
        with pytest.raises(ProviderError):
            _run(runtime, agent_id, f"hello from {agent_id}", session_id)
    finally:
        state["unavailable"] = False


def _models(requests: list[dict]) -> set[str]:
    return {request["model"] for request in requests}


def test_same_agent_continues_its_session(two_agent_runtime):
    runtime, requests, state = two_agent_runtime
    _fail_first_turn(runtime, state, agent_id="agent-a", session_id="shared")
    mark = len(requests)

    result = _run(runtime, "agent-a", "hello again", "shared")

    assert _models(requests[mark:]) == {"model-a"}
    assert result.metadata["agent"] == "agent-a"
    assert result.session_id == "shared"


def test_conflicting_explicit_agent_is_rejected_before_provider_io(
    two_agent_runtime,
):
    runtime, requests, state = two_agent_runtime
    _fail_first_turn(runtime, state, agent_id="agent-a", session_id="shared")
    mark = len(requests)

    with pytest.raises(
        TurnRequestError,
        match="Session 'shared' belongs to agent 'agent-a', not 'agent-b'",
    ):
        _run(runtime, "agent-b", "hello from agent-b", "shared")

    assert requests[mark:] == []


def _submit(runtime, agent_id: str | None, message: str, session_id: str):
    payload = {"message": message, "session_id": session_id, "timeout_seconds": 30}
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return runtime.submit_turn(payload=payload).result(timeout_s=60)


def test_submitted_conflicting_explicit_agent_fails_before_provider_io(
    two_agent_runtime,
):
    runtime, requests, state = two_agent_runtime
    _fail_first_turn(runtime, state, agent_id="agent-b", session_id="owned-by-b")
    mark = len(requests)

    result = _submit(runtime, "agent-a", "hello from agent-a", "owned-by-b")

    assert [error.code for error in result.errors] == ["invalid_request"]
    assert (
        "Session 'owned-by-b' belongs to agent 'agent-b', not 'agent-a'"
        in result.errors[0].message
    )
    assert result.final_text == ""
    assert requests[mark:] == []


@pytest.mark.parametrize("agent_id", ["agent-b", None])
def test_submitted_turn_follows_session_owner(two_agent_runtime, agent_id):
    runtime, requests, state = two_agent_runtime
    _fail_first_turn(runtime, state, agent_id="agent-b", session_id="owned-by-b")
    mark = len(requests)

    result = _submit(runtime, agent_id, "hello again", "owned-by-b")

    assert result.errors == []
    assert _models(requests[mark:]) == {"model-b"}


def test_fresh_session_uses_requested_agent(two_agent_runtime):
    runtime, requests, _state = two_agent_runtime

    result = _run(runtime, "agent-b", "hello from agent-b", "fresh")

    assert _models(requests) == {"model-b"}
    assert result.metadata["agent"] == "agent-b"


def test_submitted_idempotency_key_rejects_cross_agent_reuse(two_agent_runtime):
    runtime, requests, _state = two_agent_runtime
    payload = {
        "message": "same request",
        "session_id": "shared-idempotency-session",
        "idempotency_key": "shared-idempotency-key",
        "deliver": False,
        "timeout_seconds": 30,
    }

    first = runtime.submit_turn(payload={**payload, "agent_id": "agent-a"}).result(
        timeout_s=60
    )
    mark = len(requests)
    second = runtime.submit_turn(
        payload={
            **payload,
            "agent_id": "agent-b",
            "session_id": "other-idempotency-session",
        }
    ).result(timeout_s=60)

    assert first.errors == []
    assert [error.code for error in second.errors] == ["turn_failed"]
    assert "idempotency key conflicts" in second.errors[0].message
    assert len(requests) == mark
