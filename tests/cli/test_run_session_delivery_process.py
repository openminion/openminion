"""Real-process contract for one-shot ``openminion run`` delivery acknowledgement.

``openminion run`` executes a turn with ``deliver=False`` and returns the reply to the
caller, so it must signal ``caller_handles_delivery=true`` at both request boundaries
(in-process runtime and daemon). Without that signal the reply stays a pending outbound
and the next invocation replays it instead of running new work.

These tests drive the installed CLI in separate processes against a controlled
local OpenAI-compatible provider so the command contract, the transport boundary and
the persisted acknowledgement are locked together.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STUB_API_KEY_ENV = "OPENMINION_STUB_PROVIDER_KEY"
SESSION_ID = "run-delivery-session"


class _StubProvider:
    """Local OpenAI-compatible provider that records every chat completion request."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: list[dict[str, Any]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "_StubProvider":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=10)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._requests)

    def turn_requests(self, prompt: str) -> list[dict[str, Any]]:
        """Requests that carry ``prompt`` as the real user turn (not the classifier)."""
        return [
            request
            for request in self.requests()
            if _last_user_message(request["body"]) == prompt
        ]

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        provider = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server contract
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with provider._lock:
                    provider._requests.append({"path": self.path, "body": body})
                payload = json.dumps(_completion(body)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                return

        return _Handler


def _completion(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "stub-completion",
        "object": "chat.completion",
        "model": str(request.get("model", "stub-model")),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": f"reply to: {_last_user_message(request)}",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _last_user_message(body: dict[str, Any]) -> str:
    for message in reversed(list(body.get("messages", []))):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _history_contents(request: dict[str, Any]) -> list[str]:
    messages = list(request["body"].get("messages", []))
    return [str(message.get("content") or "") for message in messages]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_config(
    path: Path,
    *,
    base_url: str,
    storage_path: Path,
    process_mode: str,
    ipc_port: int | None = None,
) -> None:
    runtime: dict[str, Any] = {
        "process_mode": process_mode,
        "daemon_auto_start": False,
    }
    if ipc_port is not None:
        runtime.update({"ipc_host": "127.0.0.1", "ipc_port": ipc_port})
    config = {
        "agents": {"agent": {"provider": "openai", "model": "stub-model"}},
        "default_agent": "agent",
        "providers": {
            "openai": {
                "model": "stub-model",
                "base_url": base_url,
                "api_key_env": STUB_API_KEY_ENV,
            }
        },
        "runtime": runtime,
        "storage": {"path": str(storage_path)},
    }
    path.write_text(json.dumps(config) + "\n", encoding="utf-8")


def _cli_env(home_root: Path, data_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    source_path = str(PROJECT_ROOT / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        source_path if not existing else os.pathsep.join([source_path, existing])
    )
    env["OPENMINION_HOME"] = str(home_root)
    env["OPENMINION_DATA_ROOT"] = str(data_root)
    env["OPENMINION_GENERATED_ROOT"] = str(data_root / "runtime")
    env[STUB_API_KEY_ENV] = "local-stub-key"
    return env


def _run_cli(
    args: list[str], *, env: dict[str, str], timeout: int = 180
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "openminion", *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _delivered_events(storage_path: Path) -> list[dict[str, Any]]:
    if not storage_path.exists():
        return []
    connection = sqlite3.connect(f"file:{storage_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT event_type, payload_json FROM events "
            "WHERE event_type = 'response.delivered' ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return [{"event_type": row[0], "payload": json.loads(row[1])} for row in rows]


@pytest.mark.parametrize(
    ("runtime_source", "process_mode"),
    [("in-process", "single-process"), ("daemon", "daemon")],
)
def test_run_acknowledges_delivery_across_separate_processes(
    tmp_path: Path,
    runtime_source: str,
    process_mode: str,
) -> None:
    root = tmp_path / runtime_source.replace("-", "_")
    home_root = root / "home"
    data_root = root / "data"
    home_root.mkdir(parents=True, exist_ok=True)
    storage_path = data_root / "state.db"
    config_path = root / "config.json"
    ipc_port = _free_port() if process_mode == "daemon" else None

    with _StubProvider() as provider:
        _write_config(
            config_path,
            base_url=provider.base_url,
            storage_path=storage_path,
            process_mode=process_mode,
            ipc_port=ipc_port,
        )
        env = _cli_env(home_root, data_root)
        root_args = ["--config", str(config_path), "--home-root", str(home_root)]
        root_args += ["--data-root", str(data_root)]

        started = False
        if process_mode == "daemon":
            start = _run_cli([*root_args, "daemon", "start"], env=env)
            assert start.returncode == 0, start.stdout + start.stderr
            started = True
        try:
            _assert_session_contract(
                provider=provider,
                storage_path=storage_path,
                env=env,
                root_args=root_args,
            )
        finally:
            if started:
                _run_cli([*root_args, "daemon", "stop"], env=env)


def _assert_session_contract(
    *,
    provider: _StubProvider,
    storage_path: Path,
    env: dict[str, str],
    root_args: list[str],
) -> None:
    def run_cli_run(prompt: str, *extra: str) -> subprocess.CompletedProcess[str]:
        completed = _run_cli(
            [
                *root_args,
                "run",
                prompt,
                "--session",
                SESSION_ID,
                *extra,
            ],
            env=env,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert f"reply to: {prompt}" in completed.stdout, completed.stdout
        return completed

    # Cycle one: a repeated invocation with an explicit session must run new work
    # instead of replaying the settled reply, and must persist the acknowledgement.
    run_cli_run("first turn")
    first_events = _delivered_events(storage_path)
    assert len(first_events) == 1
    assert len(provider.turn_requests("first turn")) >= 1

    run_cli_run("second turn")
    second_events = _delivered_events(storage_path)
    assert len(second_events) == 2
    assert len(provider.turn_requests("second turn")) >= 1
    assert all(event["payload"]["delivery_mode"] == "return" for event in second_events)

    # Default (no --resume) forks: the new input carries no prior thread history.
    default_fork = provider.turn_requests("second turn")[-1]
    history = _history_contents(default_fork)
    assert "reply to: first turn" not in history

    # --resume continues the prior thread instead of forking.
    run_cli_run("third turn", "--resume")
    resumed = provider.turn_requests("third turn")[-1]
    resumed_history = _history_contents(resumed)
    assert "reply to: second turn" in resumed_history

    # --reset-session starts clean again.
    run_cli_run("fourth turn", "--reset-session")
    reset = provider.turn_requests("fourth turn")[-1]
    reset_history = _history_contents(reset)
    assert "reply to: third turn" not in reset_history

    final_events = _delivered_events(storage_path)
    assert len(final_events) == 4
    run_ids = [event["payload"].get("run_id") for event in final_events]
    assert len(set(run_ids)) == 4
