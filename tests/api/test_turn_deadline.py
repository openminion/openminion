"""Real SDK deadlines and handoffs through a local HTTP provider."""

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from time import monotonic, sleep

import pytest

from openminion import Agent, APIRuntime, Handoff, TurnTimeoutError, tool
from openminion.api.queries.runs import list_runs
from openminion.base.config import OpenMinionConfig, save_config
from tests._csc_fixtures import _csc_install_default_agent


@pytest.fixture
def local_sdk(tmp_path, monkeypatch):
    requests = []
    state = {"delay": False, "tool_sent": False}
    responded = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            if state["delay"]:
                sleep(2)
            names = [item["function"]["name"] for item in request.get("tools", [])]
            name = next(
                (
                    name
                    for name in names
                    if name in {"lookup_order", "transfer_to_child"}
                ),
                None,
            )
            message = {"role": "assistant", "content": "LOCAL_MARKER"}
            finish = "stop"
            if name and not state["tool_sent"]:
                state["tool_sent"] = True
                arguments = (
                    {"order_id": "A-104"}
                    if name == "lookup_order"
                    else {"message": "Return LOCAL_MARKER."}
                )
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "local-call",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            body = json.dumps(
                {
                    "id": "local-response",
                    "object": "chat.completion",
                    "model": "local",
                    "choices": [
                        {"index": 0, "message": message, "finish_reason": finish}
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 5,
                        "total_tokens": 10,
                    },
                }
            ).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                responded.set()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("LOCAL_SDK_KEY", "dummy-local")
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openai")
    config.providers.openai.api_key_env = "LOCAL_SDK_KEY"
    config.providers.openai.base_url = f"http://127.0.0.1:{server.server_port}/v1"
    # Explicit fixture authorization; no external tools or effects are used.
    config.action_policy.mode = "bypass"
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    config.storage.path = str(tmp_path / "state.db")
    config_path = tmp_path / "agents.json"
    save_config(config, str(config_path))
    runtime = APIRuntime.from_config_path(
        str(config_path), home_root=tmp_path, data_root=tmp_path / "data"
    )
    try:
        yield runtime, requests, state, responded, tmp_path
    finally:
        runtime.close()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("async_caller", [False, True])
def test_sdk_timeout_has_no_late_requests_tools_or_answer(local_sdk, async_caller):
    import asyncio

    runtime, requests, state, responded, root = local_sdk
    state["delay"] = True
    calls = []

    @tool(min_scope="READ_ONLY")
    def lookup_order(order_id: str) -> str:
        calls.append(order_id)
        return "ready"

    agent = Agent(runtime=runtime, tools=[lookup_order], forced_tools=["lookup_order"])

    async def async_turn():
        return agent.run("Check A-104", timeout_seconds=1)

    started = monotonic()
    with pytest.raises(TurnTimeoutError):
        asyncio.run(async_turn()) if async_caller else agent.run(
            "Check A-104", timeout_seconds=1
        )
    assert monotonic() - started < 1.8
    assert responded.wait(3)
    sleep(0.2)
    assert len(requests) == 1
    assert calls == []
    runs = list_runs(None, session_id=agent.session_id, runtime=runtime)["runs"]
    assert runs[0]["state"] == "failed"
    for database in root.rglob("sessions.db"):
        with sqlite3.connect(database) as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM turns WHERE role='assistant'"
                ).fetchone()[0]
                == 0
            )


def test_sdk_success_and_explicit_handoff_execute_through_real_schema(
    local_sdk, monkeypatch
):
    runtime, requests, _state, _responded, _root = local_sdk
    child = Agent(runtime=runtime, name="child")
    calls = []
    child_run = child.run

    def observed_child(*args, **kwargs):
        calls.append(args[0])
        return child_run(*args, **kwargs)

    monkeypatch.setattr(child, "run", observed_child)
    parent = Agent(
        runtime=runtime,
        handoffs=[Handoff(target=child)],
        forced_tools=["transfer_to_child"],
    )
    result = parent.run("Delegate to child.", timeout_seconds=10)
    assert result.run_state == "completed"
    assert calls == ["Return LOCAL_MARKER."]
    assert any(
        message["role"] == "tool" and "LOCAL_MARKER" in message.get("content", "")
        for request in requests
        for message in request.get("messages", [])
    )
    assert "transfer_to_child" not in runtime.tools.list()


def test_sdk_timeout_discards_running_tool_result(local_sdk):
    runtime, requests, _state, _responded, root = local_sdk
    entered, release, finished = Event(), Event(), Event()

    @tool(min_scope="READ_ONLY")
    def lookup_order(order_id: str) -> str:
        entered.set()
        try:
            release.wait(5)
            return "LATE_TOOL_MARKER"
        finally:
            finished.set()

    agent = Agent(runtime=runtime, tools=[lookup_order], forced_tools=["lookup_order"])
    started = monotonic()
    try:
        with pytest.raises(TurnTimeoutError):
            agent.run("Check A-104", timeout_seconds=1)
        assert monotonic() - started < 1.8
        assert entered.is_set()
        request_count = len(requests)
    finally:
        release.set()
    assert finished.wait(1)
    sleep(0.2)
    assert len(requests) == request_count
    for database in root.rglob("sessions.db"):
        with sqlite3.connect(database) as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM turns WHERE role='assistant'"
                ).fetchone()[0]
                == 0
            )


def test_submitted_turn_timeout_has_failed_receipt(local_sdk):
    runtime, requests, state, responded, _root = local_sdk
    state["delay"] = True
    handle = runtime.submit_turn(
        payload={
            "message": "Return a marker.",
            "session_id": "submitted-deadline",
            "timeout_seconds": 1,
        }
    )
    result = handle.result(timeout_s=3)
    assert result.final_text == ""
    assert result.errors[0].code == "turn_timeout"
    assert "timed out" in result.errors[0].message
    assert responded.wait(3)
    assert len(requests) == 1
    assert (
        list_runs(None, session_id="submitted-deadline", runtime=runtime)["runs"][0][
            "state"
        ]
        == "failed"
    )


def test_sdk_timeout_during_tool_call_keeps_session_usable(local_sdk):
    runtime, requests, _state, _responded, _root = local_sdk
    entered, release, finished = Event(), Event(), Event()

    @tool(min_scope="READ_ONLY", idempotent=True)
    def lookup_order(order_id: str) -> str:
        entered.set()
        try:
            release.wait(15)
            return "LATE_TOOL_MARKER"
        finally:
            finished.set()

    session_id = "timeout-tool-session-resume"
    agent = Agent(
        runtime=runtime,
        session_id=session_id,
        tools=[lookup_order],
        forced_tools=["lookup_order"],
    )
    follow_up = Agent(runtime=runtime, session_id=session_id)
    try:
        with pytest.raises(TurnTimeoutError):
            agent.run("Check A-104", timeout_seconds=4)
        assert entered.is_set()

        while_blocked = follow_up.run("hello", timeout_seconds=10)
        assert while_blocked.run_state == "completed"
        assert "LOCAL_MARKER" in while_blocked.text
    finally:
        release.set()

    assert finished.wait(2)
    after_finish = follow_up.run("hello again", timeout_seconds=10)
    assert after_finish.run_state == "completed"
    assert "LOCAL_MARKER" in after_finish.text
    assert not any("LATE_TOOL_MARKER" in json.dumps(req) for req in requests)
    assert any(
        msg.get("role") == "tool"
        and msg.get("tool_call_id") == "local-call"
        and "TURN_TIMEOUT" in msg.get("content", "")
        for req in requests
        for msg in req.get("messages", [])
    )
    states = [
        run["state"]
        for run in list_runs(None, session_id=session_id, runtime=runtime)["runs"]
    ]
    assert states.count("failed") == 1
    assert states.count("completed") == 2
