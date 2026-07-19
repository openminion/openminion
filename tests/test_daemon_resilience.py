from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import logging
import sys

import pytest

import openminion.daemon as daemon_mod
from openminion.cli.transport.daemon_client import (
    DaemonEndpoint,
    daemon_is_reachable,
    daemon_request,
    probe_daemon_endpoint,
    daemon_stream_request,
)
from openminion.cli.commands import daemon as daemon_cmd
from openminion.modules.telemetry.lifecycle import build_component_identity
from openminion.services.supervision import SupervisionObservation, SupervisionService


@pytest.fixture(autouse=True)
def _sync_daemon_client_symbols() -> None:
    current = sys.modules.get("openminion.cli.transport.daemon_client")
    if current is not None:
        globals()["daemon_client_mod"] = current
        globals()["DaemonEndpoint"] = current.DaemonEndpoint
        globals()["daemon_is_reachable"] = current.daemon_is_reachable
        globals()["daemon_request"] = current.daemon_request
        globals()["probe_daemon_endpoint"] = current.probe_daemon_endpoint
        globals()["daemon_stream_request"] = current.daemon_stream_request


def test_daemon_request_wraps_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = DaemonEndpoint(config_path="dummy.json", host="127.0.0.1", port=9999)

    def _raise_timeout(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(
        "openminion.cli.transport.daemon_client.urlopen", _raise_timeout
    )

    with pytest.raises(RuntimeError, match="daemon request failed: timed out"):
        daemon_request(
            endpoint=endpoint,
            method="GET",
            path="/v1/health",
            timeout_s=0.1,
        )


def test_daemon_stream_request_wraps_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = DaemonEndpoint(config_path="dummy.json", host="127.0.0.1", port=9999)

    def _raise_timeout(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(
        "openminion.cli.transport.daemon_client.urlopen", _raise_timeout
    )

    with pytest.raises(RuntimeError, match="daemon request failed: timed out"):
        daemon_stream_request(
            endpoint=endpoint,
            method="POST",
            path="/v1/turn/stream",
            payload={"message": "hello"},
            timeout_s=0.1,
        )


def test_daemon_stream_request_wraps_disconnect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = DaemonEndpoint(config_path="dummy.json", host="127.0.0.1", port=9999)

    def _raise_disconnect(*_args, **_kwargs):
        raise ConnectionResetError("connection reset by peer")

    monkeypatch.setattr(
        "openminion.cli.transport.daemon_client.urlopen", _raise_disconnect
    )

    with pytest.raises(
        RuntimeError, match="daemon request failed: connection reset by peer"
    ):
        daemon_stream_request(
            endpoint=endpoint,
            method="POST",
            path="/v1/turn/stream",
            payload={"message": "hello"},
            timeout_s=0.1,
        )


def test_daemon_is_reachable_returns_false_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = DaemonEndpoint(config_path="dummy.json", host="127.0.0.1", port=9999)

    def _raise_timeout(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(
        "openminion.cli.transport.daemon_client.daemon_request", _raise_timeout
    )
    assert daemon_is_reachable(endpoint) is False


def test_probe_daemon_endpoint_reports_mismatch_for_wrong_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = DaemonEndpoint(
        config_path="/expected/config.json",
        host="127.0.0.1",
        port=9999,
    )

    def _fake_request(*_args, **_kwargs):
        return 200, {"ok": True, "daemon": {"config_path": "/other/config.json"}}

    monkeypatch.setattr(
        "openminion.cli.transport.daemon_client.daemon_request", _fake_request
    )

    status, payload = probe_daemon_endpoint(endpoint)

    assert status == "mismatch"
    assert payload["daemon"]["config_path"] == "/other/config.json"
    assert daemon_is_reachable(endpoint) is False


def test_daemon_stream_request_parses_status_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = DaemonEndpoint(config_path="dummy.json", host="127.0.0.1", port=9999)
    events = []

    class _FakeHeaders:
        def get(self, key: str, default: str = "") -> str:
            if key.lower() == "content-type":
                return "text/event-stream"
            return default

    class _FakeResponse:
        status = 200
        headers = _FakeHeaders()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def __iter__(self):
            yield b"event: meta\n"
            yield b'data: {"trace_id":"trace-1","session_id":"sess-1"}\n'
            yield b"\n"
            yield b"event: chunk\n"
            yield b'data: {"trace_id":"trace-1","kind":"status","data":{"trace_id":"trace-1","status_key":"analyzing","label":"Analyzing request..."}}\n'
            yield b"\n"
            yield b"event: response\n"
            yield b'data: {"trace_id":"trace-1","final_text":"ok"}\n'
            yield b"\n"
            yield b"event: done\n"
            yield b'data: {"status":"complete"}\n'
            yield b"\n"
            raise AssertionError("stream parser read past terminal done event")

    monkeypatch.setattr(
        "openminion.cli.transport.daemon_client.urlopen",
        lambda *_args, **_kwargs: _FakeResponse(),
    )

    status, payload = daemon_stream_request(
        endpoint=endpoint,
        method="POST",
        path="/v1/turn/stream",
        payload={"message": "hello"},
        on_event=events.append,
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["turn"]["final_text"] == "ok"
    assert payload["chunks"][0]["kind"] == "status"
    assert len(events) == 4
    assert events[1].event == "chunk"
    assert events[1].data["kind"] == "status"


def test_daemon_stream_request_falls_back_to_json_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = DaemonEndpoint(config_path="dummy.json", host="127.0.0.1", port=9999)

    class _FakeHeaders:
        def get(self, key: str, default: str = "") -> str:
            if key.lower() == "content-type":
                return "application/json"
            return default

    class _FakeResponse:
        status = 200
        headers = _FakeHeaders()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true, "turn": {"final_text": "json ok"}}'

    monkeypatch.setattr(
        "openminion.cli.transport.daemon_client.urlopen",
        lambda *_args, **_kwargs: _FakeResponse(),
    )

    status, payload = daemon_stream_request(
        endpoint=endpoint,
        method="POST",
        path="/v1/turn/stream",
        payload={"message": "hello"},
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["turn"]["final_text"] == "json ok"


def test_daemon_stop_force_kills_hung_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pid_file = tmp_path / "openminiond.pid"
    pid_file.write_text("123\n", encoding="utf-8")
    endpoint = DaemonEndpoint(
        config_path="dummy.json",
        host="127.0.0.1",
        port=18789,
    )

    state = {"killed": False, "now": 0.0}

    def _fake_time() -> float:
        state["now"] += 1.0
        return state["now"]

    def _fake_alive(pid: int) -> bool:
        assert pid == 123
        return not state["killed"]

    def _fake_kill(pid: int, sig: int) -> None:
        assert pid == 123
        if sig == daemon_cmd.signal.SIGKILL:
            state["killed"] = True

    monkeypatch.setattr(daemon_cmd, "resolve_daemon_endpoint", lambda _cfg: endpoint)
    monkeypatch.setattr(daemon_cmd, "load_config", lambda _path: SimpleNamespace())
    monkeypatch.setattr(daemon_mod, "resolve_daemon_pid_file", lambda _cfg: pid_file)
    monkeypatch.setattr(daemon_mod, "read_pid", lambda _pid_file: 123)
    monkeypatch.setattr(daemon_mod, "process_alive", _fake_alive)
    monkeypatch.setattr(daemon_cmd.os, "kill", _fake_kill)
    monkeypatch.setattr(daemon_cmd.time, "time", _fake_time)
    monkeypatch.setattr(daemon_cmd.time, "sleep", lambda _s: None)

    code = daemon_cmd.daemon_stop(config_path="dummy.json")
    output = capsys.readouterr().out

    assert code == 0
    assert "Force-stopped daemon pid=123 after graceful timeout." in output
    assert not pid_file.exists()


def test_daemon_lifecycle_emitter_records_native_canonical_events(
    tmp_path: Path,
) -> None:
    recorded = []

    class FakeTelemetryService:
        def record_event_sync(self, event) -> None:
            recorded.append(event)

        def close_sync(self) -> None:
            return None

    emitter = daemon_mod._DaemonLifecycleEmitter(
        home_root=tmp_path,
        env={},
        pid=321,
        bind_host="127.0.0.1",
        bind_port=18789,
        telemetry_service=FakeTelemetryService(),
        heartbeat_interval_seconds=1.0,
    )

    emitter.emit_started()
    emitter.emit_heartbeat()
    emitter.emit_stopped(reason="signal_stop")
    emitter.emit_crashed(reason="server_error", error=RuntimeError("boom"))
    emitter.close()

    assert [event.event_type for event in recorded] == [
        "component.started",
        "component.heartbeat",
        "component.stopped",
        "component.crashed",
    ]
    assert recorded[0].data["source_classification"] == "native_canonical"
    assert recorded[0].data["component"]["component_kind"] == "daemon"
    assert recorded[0].session_id == "lifecycle:daemon:primary"
    assert recorded[1].session_id == "lifecycle:daemon:primary"
    assert recorded[1].data["component"]["component_id"] == "primary"
    assert recorded[1].data["metrics"]["uptime_seconds"] >= 0
    assert recorded[2].data["reason"] == "signal_stop"
    assert recorded[3].data["evidence"]["error_class"] == "RuntimeError"


def test_build_daemon_supervision_policy_uses_explicit_heartbeat_multipliers() -> None:
    policy = daemon_mod.build_daemon_supervision_policy(heartbeat_interval_seconds=15)

    assert policy.stale_heartbeat_warn_after_seconds == 30
    assert policy.stale_heartbeat_fail_after_seconds == 60
    assert policy.restart_enabled is False


def test_build_daemon_supervision_policy_accepts_restart_backoff_settings() -> None:
    policy = daemon_mod.build_daemon_supervision_policy(
        heartbeat_interval_seconds=15,
        restart_enabled=True,
        restart_max_attempts=4,
        restart_initial_backoff_seconds=10,
        restart_max_backoff_seconds=90,
        crash_loop_threshold=5,
    )

    assert policy.restart_enabled is True
    assert policy.restart_max_attempts == 4
    assert policy.restart_initial_backoff_seconds == 10
    assert policy.restart_max_backoff_seconds == 90
    assert policy.crash_loop_threshold == 5


def test_daemon_supervision_policy_marks_recent_heartbeat_healthy() -> None:
    now = "2026-03-19T06:00:00+00:00"
    component = build_component_identity(
        component_kind="daemon",
        component_id="primary",
        scope="system",
        owner_module="openminion-runtime",
    )
    decision = SupervisionService().evaluate(
        observation=SupervisionObservation(
            component=component,
            latest_event_type="component.heartbeat",
            latest_observed_at=now,
            last_heartbeat_at=now,
        ),
        policy=daemon_mod.build_daemon_supervision_policy(
            heartbeat_interval_seconds=15
        ),
        observed_at=datetime.fromisoformat(now),
    )

    assert decision.posture == "healthy"
    assert decision.reason == "lifecycle_healthy"


def test_daemon_supervision_policy_marks_stale_heartbeat_degraded_then_failed() -> None:
    component = build_component_identity(
        component_kind="daemon",
        component_id="primary",
        scope="system",
        owner_module="openminion-runtime",
    )
    service = SupervisionService()
    policy = daemon_mod.build_daemon_supervision_policy(heartbeat_interval_seconds=15)
    heartbeat_at = "2026-03-19T06:00:00+00:00"
    observation = SupervisionObservation(
        component=component,
        latest_event_type="component.started",
        latest_observed_at=heartbeat_at,
        last_heartbeat_at=heartbeat_at,
    )

    degraded = service.evaluate(
        observation=observation,
        policy=policy,
        observed_at=datetime.fromisoformat("2026-03-19T06:00:31+00:00"),
    )
    failed = service.evaluate(
        observation=observation,
        policy=policy,
        observed_at=datetime.fromisoformat("2026-03-19T06:01:01+00:00"),
    )

    assert degraded.posture == "degraded"
    assert degraded.reason == "stale_heartbeat_degraded"
    assert failed.posture == "failed"
    assert failed.reason == "stale_heartbeat_failed"


def test_attach_cron_scheduler_injects_cron_metadata_into_turn_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _FakeHandle:
        def __init__(self, response) -> None:
            self._response = response

        def result(self, timeout_s: float = 300.0):  # noqa: ANN001
            del timeout_s
            return self._response

    class _FakeRuntimeManager:
        def __init__(self) -> None:
            self.submitted = None

        def submit_turn(self, request):  # noqa: ANN001
            self.submitted = request
            return _FakeHandle(SimpleNamespace(final_text="cron ok"))

    class _FakeCronStore:
        def __init__(self, db_path: Path) -> None:
            self.db_path = db_path

        def add_cron_job(self, **_kwargs):  # noqa: ANN003
            return "system-cron-cleanup"

        def delete_old_cron_runs(self, _cutoff: str) -> int:
            return 0

    class _FakeScheduler:
        def __init__(self, **kwargs):  # noqa: ANN003
            self.execute_agent_turn = kwargs["execute_agent_turn"]
            self.started = False

        def start(self) -> None:
            self.started = True

    runtime_manager = _FakeRuntimeManager()
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            runtime=SimpleNamespace(env={}),
            storage=SimpleNamespace(path=str(tmp_path / "api.db")),
            agent=SimpleNamespace(name="agent-cron"),
        ),
        runtime_manager=runtime_manager,
        list_registered_agents=lambda: ["agent-cron", "cron-agent-explicit"],
    )

    monkeypatch.setattr(
        "openminion.modules.storage.runtime.sqlite.resolve_database_path",
        lambda storage_path, env=None: Path(storage_path),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "openminion.modules.brain.paths.resolve_brain_sessions_db_path",
        lambda storage_path: Path(storage_path),
    )
    monkeypatch.setattr(
        "openminion.modules.session.storage.sqlite_store.SQLiteSessionStore",
        _FakeCronStore,
    )
    monkeypatch.setattr(
        "openminion.services.cron.scheduler.CronScheduler",
        _FakeScheduler,
    )

    scheduler = daemon_mod.attach_cron_scheduler(
        runtime=runtime,
        daemon_id="daemon-cron-metadata",
    )
    assert scheduler is not None

    result = scheduler.execute_agent_turn(
        {
            "job_id": "job-abc",
            "agent_id": "cron-agent-explicit",
            "payload": {"kind": "agentTurn", "message": "cron message"},
        },
        {
            "run_id": "run-def",
            "due_at": "2026-03-20T00:00:00Z",
            "isolated_session_id": "cron-run-session-1",
        },
    )
    assert result["summary"] == "cron ok"
    assert runtime_manager.submitted is not None
    assert runtime_manager.submitted.agent_id == "cron-agent-explicit"
    assert runtime_manager.submitted.session_id == "cron-run-session-1"
    assert runtime_manager.submitted.meta.get("cron_job_id") == "job-abc"
    assert runtime_manager.submitted.meta.get("cron_run_id") == "run-def"
    assert runtime_manager.submitted.meta.get("scheduled_for") == "2026-03-20T00:00:00Z"


def test_attach_cron_scheduler_rejects_unknown_job_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _FakeHandle:
        def result(self, timeout_s: float = 0):  # noqa: ARG002
            return SimpleNamespace(chunks=[])

    class _FakeRuntimeManager:
        def __init__(self) -> None:
            self.submitted = None

        def submit_turn(self, req):  # noqa: ANN001
            self.submitted = req
            return _FakeHandle()

    class _FakeCronStore:
        def __init__(self, db_path: Path) -> None:
            self.db_path = db_path

        def add_cron_job(self, **_kwargs):  # noqa: ANN003
            return "system-cron-cleanup"

        def delete_old_cron_runs(self, _cutoff: str) -> int:
            return 0

    class _FakeScheduler:
        def __init__(self, **kwargs):  # noqa: ANN003
            self.execute_agent_turn = kwargs["execute_agent_turn"]

        def start(self) -> None:
            return None

    runtime_manager = _FakeRuntimeManager()
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            runtime=SimpleNamespace(env={}),
            storage=SimpleNamespace(path=str(tmp_path / "api.db")),
            agent=SimpleNamespace(name="agent-cron"),
        ),
        runtime_manager=runtime_manager,
        list_registered_agents=lambda: ["agent-cron"],
    )

    monkeypatch.setattr(
        "openminion.modules.storage.runtime.sqlite.resolve_database_path",
        lambda storage_path, env=None: Path(storage_path),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "openminion.modules.brain.paths.resolve_brain_sessions_db_path",
        lambda storage_path: Path(storage_path),
    )
    monkeypatch.setattr(
        "openminion.modules.session.storage.sqlite_store.SQLiteSessionStore",
        _FakeCronStore,
    )
    monkeypatch.setattr(
        "openminion.services.cron.scheduler.CronScheduler",
        _FakeScheduler,
    )

    scheduler = daemon_mod.attach_cron_scheduler(
        runtime=runtime,
        daemon_id="daemon-cron-agent-check",
    )
    assert scheduler is not None

    result = scheduler.execute_agent_turn(
        {
            "job_id": "job-unknown",
            "agent_id": "wrong-agent",
            "payload": {"kind": "agentTurn", "message": "cron message"},
        },
        {"run_id": "run-unknown", "due_at": "2026-03-20T00:00:00Z"},
    )

    assert result["error"] is True
    assert "not registered" in result["summary"]
    assert runtime_manager.submitted is None


def test_attach_cron_scheduler_announce_delivery_writes_session_surface(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _FakeSessions:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []
            self.events: list[dict[str, object]] = []

        def append_message(self, **kwargs):  # noqa: ANN003
            self.messages.append(dict(kwargs))
            return SimpleNamespace(id="msg-1")

        def append_event(self, **kwargs):  # noqa: ANN003
            self.events.append(dict(kwargs))
            return SimpleNamespace(id="evt-1")

    class _FakeCronStore:
        def __init__(self, db_path: Path) -> None:
            self.db_path = db_path

        def add_cron_job(self, **_kwargs):  # noqa: ANN003
            return "system-cron-cleanup"

        def delete_old_cron_runs(self, _cutoff: str) -> int:
            return 0

    class _FakeScheduler:
        def __init__(self, **kwargs):  # noqa: ANN003
            self.delivery_handler = kwargs["delivery_handler"]

        def start(self) -> None:
            return None

    runtime = SimpleNamespace(
        config=SimpleNamespace(
            runtime=SimpleNamespace(env={}),
            storage=SimpleNamespace(path=str(tmp_path / "api.db")),
            agent=SimpleNamespace(name="agent-cron"),
        ),
        runtime_manager=SimpleNamespace(submit_turn=lambda _req: None),
        sessions=_FakeSessions(),
    )

    monkeypatch.setattr(
        "openminion.modules.storage.runtime.sqlite.resolve_database_path",
        lambda storage_path, env=None: Path(storage_path),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "openminion.modules.brain.paths.resolve_brain_sessions_db_path",
        lambda storage_path: Path(storage_path),
    )
    monkeypatch.setattr(
        "openminion.modules.session.storage.sqlite_store.SQLiteSessionStore",
        _FakeCronStore,
    )
    monkeypatch.setattr(
        "openminion.services.cron.scheduler.CronScheduler",
        _FakeScheduler,
    )

    scheduler = daemon_mod.attach_cron_scheduler(
        runtime=runtime,
        daemon_id="daemon-cron-delivery",
    )
    assert scheduler is not None

    scheduler.delivery_handler(
        "announce",
        "last",
        {
            "job_id": "job-abc",
            "payload": {
                "kind": "agentTurn",
                "message": "cron message",
                "_openminion_origin": {
                    "session_id": "sess-123",
                    "conversation_id": "conv-123",
                    "thread_id": "thread-123",
                    "attach_id": "att-123",
                },
            },
        },
        {"run_id": "run-def", "due_at": "2026-03-20T00:00:00Z"},
        {"summary": "scheduled result"},
    )

    assert runtime.sessions.messages, (
        "Expected cron announce to append a session message"
    )
    message = runtime.sessions.messages[-1]
    assert message["session_id"] == "sess-123"
    assert message["conversation_id"] == "conv-123"
    assert message["thread_id"] == "thread-123"
    assert message["attach_id"] == "att-123"
    assert message["role"] == "outbound"
    assert message["body"] == "scheduled result"
    metadata = message.get("metadata", {})
    assert isinstance(metadata, dict)
    assert metadata.get("cron_announce") == "true"
    assert metadata.get("cron_job_id") == "job-abc"
    assert metadata.get("cron_run_id") == "run-def"

    assert runtime.sessions.events, "Expected cron announce to append a session event"
    event = runtime.sessions.events[-1]
    assert event["session_id"] == "sess-123"
    assert event["event_type"] == "cron.announce"
    payload = event.get("payload", {})
    assert isinstance(payload, dict)
    assert payload.get("cron_job_id") == "job-abc"
    assert payload.get("cron_run_id") == "run-def"


def test_run_server_emits_daemon_started_heartbeat_and_stopped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "openminiond.pid"
    config = SimpleNamespace(
        runtime=SimpleNamespace(
            env={}, daemon_pid_file=str(pid_file), ipc_host="127.0.0.1", ipc_port=18789
        ),
        gateway=SimpleNamespace(host="127.0.0.1", port=18789),
    )
    manager = SimpleNamespace(
        config_path=tmp_path / "config.json",
        base_config=config,
        home_root=tmp_path,
    )
    calls: list[str] = []

    class FakeServer:
        _runtime = object()

        def serve_forever(self, poll_interval: float = 0.2) -> None:
            calls.append(f"serve_forever:{poll_interval}")

        def shutdown(self) -> None:
            calls.append("server.shutdown")

        def server_close(self) -> None:
            calls.append("server.server_close")

    class FakeLifecycleEmitter:
        def __init__(self, **kwargs) -> None:
            calls.append(f"lifecycle.init:{kwargs['pid']}")

        def emit_started(self) -> None:
            calls.append("lifecycle.started")

        def emit_heartbeat(self) -> None:
            calls.append("lifecycle.heartbeat")

        def start_heartbeat(self, *, stop_event) -> None:
            del stop_event
            calls.append("lifecycle.heartbeat_thread.start")

        def stop_heartbeat(self) -> None:
            calls.append("lifecycle.heartbeat_thread.stop")

        def emit_stopped(self, *, reason: str) -> None:
            calls.append(f"lifecycle.stopped:{reason}")

        def emit_crashed(self, *, reason: str, error: Exception | None = None) -> None:
            del error
            calls.append(f"lifecycle.crashed:{reason}")

        def close(self) -> None:
            calls.append("lifecycle.close")

    monkeypatch.setattr(daemon_mod.ConfigManager, "load", lambda _path: manager)
    monkeypatch.setattr(daemon_mod, "bootstrap_config_manager", lambda _manager: None)
    monkeypatch.setattr(daemon_mod, "build_api_server", lambda **_kwargs: FakeServer())
    monkeypatch.setattr(daemon_mod, "attach_cron_scheduler", lambda **_kwargs: None)
    monkeypatch.setattr(daemon_mod, "_DaemonLifecycleEmitter", FakeLifecycleEmitter)
    monkeypatch.setattr(daemon_mod.os, "getpid", lambda: 4321)
    monkeypatch.setattr(daemon_mod.signal, "signal", lambda *_args, **_kwargs: None)

    assert daemon_mod.run_server(config_path="dummy.json") == 0
    assert pid_file.exists() is False
    assert calls == [
        "lifecycle.init:4321",
        "lifecycle.started",
        "lifecycle.heartbeat",
        "lifecycle.heartbeat_thread.start",
        "serve_forever:0.2",
        "lifecycle.heartbeat_thread.stop",
        "server.server_close",
        "lifecycle.stopped:server_stop",
        "lifecycle.close",
    ]


def test_run_server_emits_daemon_crashed_on_server_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "openminiond.pid"
    config = SimpleNamespace(
        runtime=SimpleNamespace(
            env={}, daemon_pid_file=str(pid_file), ipc_host="127.0.0.1", ipc_port=18789
        ),
        gateway=SimpleNamespace(host="127.0.0.1", port=18789),
    )
    manager = SimpleNamespace(
        config_path=tmp_path / "config.json",
        base_config=config,
        home_root=tmp_path,
    )
    calls: list[str] = []

    class FakeServer:
        _runtime = object()

        def serve_forever(self, poll_interval: float = 0.2) -> None:
            del poll_interval
            raise RuntimeError("boom")

        def shutdown(self) -> None:
            calls.append("server.shutdown")

        def server_close(self) -> None:
            calls.append("server.server_close")

    class FakeLifecycleEmitter:
        def __init__(self, **kwargs) -> None:
            calls.append(f"lifecycle.init:{kwargs['pid']}")

        def emit_started(self) -> None:
            calls.append("lifecycle.started")

        def emit_heartbeat(self) -> None:
            calls.append("lifecycle.heartbeat")

        def start_heartbeat(self, *, stop_event) -> None:
            del stop_event
            calls.append("lifecycle.heartbeat_thread.start")

        def stop_heartbeat(self) -> None:
            calls.append("lifecycle.heartbeat_thread.stop")

        def emit_stopped(self, *, reason: str) -> None:
            calls.append(f"lifecycle.stopped:{reason}")

        def emit_crashed(self, *, reason: str, error: Exception | None = None) -> None:
            calls.append(f"lifecycle.crashed:{reason}:{type(error).__name__}")

        def close(self) -> None:
            calls.append("lifecycle.close")

    monkeypatch.setattr(daemon_mod.ConfigManager, "load", lambda _path: manager)
    monkeypatch.setattr(daemon_mod, "bootstrap_config_manager", lambda _manager: None)
    monkeypatch.setattr(daemon_mod, "build_api_server", lambda **_kwargs: FakeServer())
    monkeypatch.setattr(daemon_mod, "attach_cron_scheduler", lambda **_kwargs: None)
    monkeypatch.setattr(daemon_mod, "_DaemonLifecycleEmitter", FakeLifecycleEmitter)
    monkeypatch.setattr(daemon_mod.os, "getpid", lambda: 9999)
    monkeypatch.setattr(daemon_mod.signal, "signal", lambda *_args, **_kwargs: None)

    assert daemon_mod.run_server(config_path="dummy.json") == 1
    assert pid_file.exists() is False
    assert calls == [
        "lifecycle.init:9999",
        "lifecycle.started",
        "lifecycle.heartbeat",
        "lifecycle.heartbeat_thread.start",
        "lifecycle.crashed:server_error:RuntimeError",
        "lifecycle.heartbeat_thread.stop",
        "server.server_close",
        "lifecycle.close",
    ]


def test_run_server_wires_central_file_handler_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "openminiond.pid"
    daemon_log_file = tmp_path / "runtime" / "openminiond.log"
    config = SimpleNamespace(
        runtime=SimpleNamespace(
            env={},
            daemon_pid_file=str(pid_file),
            daemon_log_file=str(daemon_log_file),
            log_level="INFO",
            ipc_host="127.0.0.1",
            ipc_port=18789,
        ),
        gateway=SimpleNamespace(host="127.0.0.1", port=18789),
    )
    manager = SimpleNamespace(
        config_path=tmp_path / "config.json",
        base_config=config,
        home_root=tmp_path,
    )

    configure_calls: list[tuple[str, str, str]] = []

    class FakeServer:
        _runtime = object()

        def serve_forever(self, poll_interval: float = 0.2) -> None:
            del poll_interval
            return

        def shutdown(self) -> None:
            return

        def server_close(self) -> None:
            return

    class FakeLifecycleEmitter:
        def __init__(self, **_kwargs) -> None:
            return

        def emit_started(self) -> None:
            return

        def emit_heartbeat(self) -> None:
            return

        def start_heartbeat(self, *, stop_event) -> None:
            del stop_event
            return

        def stop_heartbeat(self) -> None:
            return

        def emit_stopped(self, *, reason: str) -> None:
            del reason
            return

        def emit_crashed(self, *, reason: str, error: Exception | None = None) -> None:
            del reason, error
            return

        def close(self) -> None:
            return

    def _record_configure(level, *, mode="default", file_path=None, file_level="DEBUG"):  # noqa: ANN001
        configure_calls.append((str(level), str(mode), str(file_path)))
        return logging.getLogger("openminion.test")

    monkeypatch.setattr(daemon_mod.ConfigManager, "load", lambda _path: manager)
    monkeypatch.setattr(daemon_mod, "bootstrap_config_manager", lambda _manager: None)
    monkeypatch.setattr(daemon_mod, "build_api_server", lambda **_kwargs: FakeServer())
    monkeypatch.setattr(daemon_mod, "attach_cron_scheduler", lambda **_kwargs: None)
    monkeypatch.setattr(daemon_mod, "_DaemonLifecycleEmitter", FakeLifecycleEmitter)
    monkeypatch.setattr(daemon_mod, "configure_logging", _record_configure)
    monkeypatch.setattr(daemon_mod.os, "getpid", lambda: 6001)
    monkeypatch.setattr(daemon_mod.signal, "signal", lambda *_args, **_kwargs: None)

    assert daemon_mod.run_server(config_path="dummy.json") == 0
    assert configure_calls
    level, mode, file_path = configure_calls[0]
    assert level == "INFO"
    assert mode == "daemon"
    assert file_path == str(daemon_log_file.resolve())
