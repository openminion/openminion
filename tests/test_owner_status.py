import io
import os
from contextlib import redirect_stdout

from openminion.api.runtime import APIRuntime
from openminion.api.server import dispatch_request
from openminion.base.config import OpenMinionConfig, save_config
from openminion.services.diagnostics.owner_status import build_owner_status
from openminion.services.runtime.run_status import (
    RUN_STATE_COMPLETED,
    RUN_STATE_FAILED,
    RUN_STATE_QUEUED,
    append_run_state_event,
)
from tests._csc_fixtures import _csc_install_default_agent


def _write_echo_config(tmp_path) -> str:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "owner-status.db")
    save_config(config, str(config_path))
    return str(config_path)


def _write_api_owner_echo_config(tmp_path) -> str:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    os.environ["OPENMINION_DATA_ROOT"] = str(tmp_path / ".openminion")
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "api.db")
    save_config(config, str(config_path))
    return str(config_path)


def _build_owner_status_payload(config_path: str, runtime) -> dict:
    return build_owner_status(
        config_path,
        runtime=runtime,
        session_limit=20,
        run_limit_per_session=20,
        window_hours=24,
    )


def test_build_owner_status_aggregates_run_states(tmp_path) -> None:
    config_path = _write_echo_config(tmp_path)
    runtime = APIRuntime.from_config_path(config_path)
    try:
        session_ok = runtime.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="owner-ok",
        )
        append_run_state_event(
            runtime.sessions,
            session_id=session_ok.id,
            run_id="run-ok",
            state=RUN_STATE_QUEUED,
            current_step="turn.accepted",
            payload={"request_id": "req-1"},
        )
        append_run_state_event(
            runtime.sessions,
            session_id=session_ok.id,
            run_id="run-ok",
            state=RUN_STATE_COMPLETED,
            current_step="turn.completed",
            payload={"request_id": "req-1"},
        )

        session_fail = runtime.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="owner-fail",
        )
        append_run_state_event(
            runtime.sessions,
            session_id=session_fail.id,
            run_id="run-fail",
            state=RUN_STATE_FAILED,
            current_step="turn.failed",
            payload={"error": "network timeout", "request_id": "req-2"},
        )

        payload = _build_owner_status_payload(config_path, runtime)
    finally:
        runtime.close()

    assert payload["sessions_total"] >= 2
    assert payload["summary"]["runs_total"] == 2
    assert payload["summary"]["failed_runs"] == 1
    assert payload["summary"]["completed_runs"] == 1
    assert payload["heartbeat"]["status"] == "warn"
    assert payload["recent_failures"]
    vocab = payload["component_vocabulary"]
    assert vocab["runtime"]["component_kind"] == "runtime_manager"
    assert vocab["runtime"]["component_id"] == "primary"
    assert vocab["provider"]["component_kind"] == "provider_binding"
    assert vocab["provider"]["component_id"] == "echo"
    assert vocab["channel"]["component_kind"] == "channel_adapter"
    assert vocab["channel"]["component_id"] == "console"


def test_build_owner_status_idle_when_no_runs(tmp_path) -> None:
    config_path = _write_echo_config(tmp_path)
    runtime = APIRuntime.from_config_path(config_path)
    try:
        runtime.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="owner-idle",
        )
        payload = _build_owner_status_payload(config_path, runtime)
    finally:
        runtime.close()

    assert payload["summary"]["runs_total"] == 0
    assert payload["heartbeat"]["status"] == "idle"
    assert payload["summary"]["failed_runs"] == 0


def test_get_owner_status_returns_digest_summary(tmp_path) -> None:
    config_path = _write_api_owner_echo_config(tmp_path)
    with redirect_stdout(io.StringIO()):
        dispatch_request(
            "POST",
            "/turns",
            config_path,
            body={"message": "owner-ok", "session_id": "owner-session-ok"},
            request_id="owner-turn-1",
        )
        dispatch_request(
            "POST",
            "/turns",
            config_path,
            body={
                "message": "owner-fail",
                "session_id": "owner-session-fail",
                "channel": "missing-channel",
            },
            request_id="owner-turn-2",
        )

    status, payload = dispatch_request(
        "GET",
        "/owner/status",
        config_path,
        query="session_limit=25&run_limit=25&hours=24",
        request_id="owner-status-1",
    )
    assert int(status) == 200
    assert payload["ok"]
    assert payload["meta"]["request_id"] == "owner-status-1"
    assert payload["window_hours"] == 24
    assert payload["summary"]["runs_total"] >= 2
    assert payload["summary"]["failed_runs"] >= 1
    assert payload["sessions_total"] >= 2
    assert "alerts" in payload
    assert "component_vocabulary" in payload

    health_status, health_payload = dispatch_request(
        "GET",
        "/health",
        config_path,
        request_id="owner-status-health-1",
    )
    assert int(health_status) == 200
    owner_vocab = payload["component_vocabulary"]
    assert owner_vocab["runtime"]["component_id"] == "primary"
    assert owner_vocab["provider"]["component_id"] == health_payload["provider"]
    assert owner_vocab["channel"]["component_id"] == health_payload["default_channel"]


def test_get_owner_status_invalid_query_returns_bad_request(tmp_path) -> None:
    config_path = _write_api_owner_echo_config(tmp_path)
    status, payload = dispatch_request(
        "GET",
        "/owner/status",
        config_path,
        query="hours=nope",
    )
    assert int(status) == 400
    assert not payload["ok"]
    assert payload["error"]["code"] == "invalid_request"
