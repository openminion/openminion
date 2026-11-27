"""Actual Agent, approval, and session-producer paths for the session timeline.

The real `Agent.run` cases use a local scripted OpenAI-compatible server on
127.0.0.1 and the configured `APIRuntime` storage, not a hosted model. The
handoff case uses the session module store's `bind_agent` producer because the
configured storage runtime has no bind_agent and emits neither agent event.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from openminion import Agent, APIRuntime, tool
from openminion.base.config import OpenMinionConfig, save_config
from openminion.cli.presentation.session_timeline import (
    build_timeline,
    timeline_payload,
)
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.storage.runtime.session_store import EventRecord
from tests._csc_fixtures import _csc_install_default_agent

SESSION_ID = "timeline-probe"
PROVIDER_KEY_ENV = "TIMELINE_PROVIDER_KEY"


def _completion(message: dict[str, Any], finish: str) -> dict[str, Any]:
    return {
        "id": "local",
        "object": "chat.completion",
        "model": "local",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
    }


class _Provider:
    """Scripted OpenAI-compatible server that calls one tool once."""

    def __init__(self, tool_name: str, arguments: dict[str, Any]) -> None:
        self._tool_name = tool_name
        self._arguments = arguments
        self._sent = False
        self._lock = threading.Lock()
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(length))
                response = json.dumps(provider.respond(body)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *_args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def respond(self, body: dict[str, Any]) -> dict[str, Any]:
        names = [tool["function"]["name"] for tool in body.get("tools") or []]
        with self._lock:
            call_now = not self._sent and self._tool_name in names
            self._sent = self._sent or call_now
        if call_now:
            call = {
                "id": f"call-{self._tool_name}",
                "type": "function",
                "function": {
                    "name": self._tool_name,
                    "arguments": json.dumps(self._arguments),
                },
            }
            return _completion(
                {"role": "assistant", "content": None, "tool_calls": [call]},
                "tool_calls",
            )
        return _completion(
            {"role": "assistant", "content": json.dumps({"answer": "DONE"})},
            "stop",
        )

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _runtime(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: _Provider,
) -> APIRuntime:
    monkeypatch.setenv(PROVIDER_KEY_ENV, "local-dummy-key")
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openai")
    config.providers.openai.api_key_env = PROVIDER_KEY_ENV
    config.providers.openai.base_url = provider.base_url
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    config.storage.path = str(root / "state.db")
    save_config(config, str(root / "agents.json"))
    return APIRuntime.from_config_path(
        str(root / "agents.json"),
        home_root=root,
        data_root=root / "data",
    )


def _timeline(runtime: APIRuntime) -> tuple[Any, Any, Any]:
    messages = runtime.sessions.list_recent_messages(
        session_id=SESSION_ID,
        limit=500,
    )
    events = runtime.sessions.list_events(
        session_id=SESSION_ID,
        limit=500,
        newest_first=True,
    )
    return messages, events, build_timeline(messages=messages, events=events)


def test_real_read_only_tool_turn_shows_messages_and_tool_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @tool(min_scope="READ_ONLY")
    def probe_tool(order_id: str) -> str:
        """Read one probe order."""
        return f"probe-ok-{order_id}"

    provider = _Provider("probe_tool", {"order_id": "A-104"})
    runtime = _runtime(tmp_path, monkeypatch, provider)
    try:
        result = Agent(
            runtime=runtime,
            session_id=SESSION_ID,
            tools=[probe_tool],
            forced_tools=["probe_tool"],
        ).run("Use the probe tool.")
        assert result.run_state == "completed"
        messages, events, entries = _timeline(runtime)
    finally:
        runtime.close()
        provider.close()

    assert [message.role for message in messages] == ["inbound", "outbound"]
    inbound = next(
        entry for entry in entries if entry.kind == "message" and entry.actor == "user"
    )
    assistant = next(
        entry
        for entry in entries
        if entry.kind == "message" and entry.actor == "assistant"
    )
    tool_entry = next(entry for entry in entries if entry.kind == "tool")
    assert inbound.summary == "Use the probe tool."
    assert "DONE" in assistant.summary
    assert tool_entry.status == "ok"
    assert tool_entry.event_type == "tool.execution.completed"
    assert "probe_tool" in tool_entry.summary
    assert "probe-ok-A-104" in tool_entry.summary
    assert entries.index(tool_entry) < entries.index(assistant)
    assert any(event.event_type == "run.waiting_tool" for event in events)


def test_real_approval_prompt_maps_the_prompt_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    @tool
    def write_probe(tag: str) -> str:
        """Write one probe tag."""
        calls.append(tag)
        return f"wrote-{tag}"

    provider = _Provider("write_probe", {"tag": "A-104"})
    runtime = _runtime(tmp_path, monkeypatch, provider)
    try:
        result = Agent(
            runtime=runtime,
            session_id=SESSION_ID,
            tools=[write_probe],
            forced_tools=["write_probe"],
        ).run("Write the probe tag.")
        assert result.metadata["respond_kind"] == "policy_confirmation_prompt"
        assert calls == []
        _messages, events, entries = _timeline(runtime)
        prompts = [
            event
            for event in events
            if event.event_type == "policy_confirmation_prompt"
        ]
    finally:
        runtime.close()
        provider.close()

    assert len(prompts) == 1
    body = str(prompts[0].payload["body"])
    flat = " ".join(body.split())
    expected = flat if len(flat) <= 160 else f"{flat[:157]}..."
    approval = next(entry for entry in entries if entry.kind == "approval")
    assert approval.actor == "policy"
    assert approval.status == "pending"
    assert approval.summary == expected
    assert not any(entry.kind == "tool" for entry in entries)


def test_bind_agent_producer_maps_to_handoff_and_binding(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    try:
        session_id = store.create_session(
            initial_agent_id="agent.alpha",
            profile_version="pv1",
        )
        store.bind_agent(
            session_id,
            agent_id="agent.beta",
            profile_version="pv2",
            reason="handoff",
        )
        switched = store.get_events(session_id, types=["agent.switched"])
        bound = store.get_events(session_id, types=["agent.bound"])
    finally:
        store.close()

    switched_payload = dict(switched[-1]["payload"])
    bound_payload = dict(bound[-1]["payload"])
    assert switched_payload == {
        "from_agent_id": "agent.alpha",
        "to_agent_id": "agent.beta",
        "reason": "handoff",
    }
    assert "to_agent" not in switched_payload
    assert bound_payload["agent_id"] == "agent.beta"
    assert bound_payload["profile_version"] == "pv2"

    entries = build_timeline(
        events=[
            _event_record(switched[-1]),
            _event_record(bound[-1]),
        ]
    )

    handoff = next(entry for entry in entries if entry.kind == "handoff")
    binding = next(entry for entry in entries if entry.kind == "event")
    assert handoff.event_type == "agent.switched"
    assert handoff.summary == "agent.alpha -> agent.beta (handoff)"
    assert binding.event_type == "agent.bound"
    assert binding.kind == "event"
    assert binding.summary == "agent bound agent.beta (profile pv2)"
    encoded = json.dumps(timeline_payload(session_id=session_id, entries=entries))
    assert "to_agent_id" not in encoded


def _event_record(row: dict[str, Any]) -> EventRecord:
    return EventRecord(
        id=int(row["seq"]),
        session_id=str(row["session_id"]),
        event_type=str(row["event_type"]),
        payload=dict(row["payload"]),
        created_at=str(row["timestamp"]),
        canonical_event_id=str(row["event_id"]),
    )
