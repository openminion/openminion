import io
from pathlib import Path
from contextlib import redirect_stdout

import pytest

from tests._csc_fixtures import _csc_install_default_agent

from openminion.api.server import dispatch_request
from openminion.base.config import OpenMinionConfig, save_config


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _write_echo_config(tmp_path, monkeypatch)


def test_get_session_runs_returns_lifecycle_summary(config_path: Path) -> None:
    session_id = "session-runs-1"
    with redirect_stdout(io.StringIO()):
        turn_status, turn_payload = dispatch_request(
            "POST",
            "/turns",
            str(config_path),
            body={"message": "run status", "session_id": session_id},
        )
    assert int(turn_status) == 200
    run_id = turn_payload["turn"]["run_id"]

    status, payload = dispatch_request(
        "GET",
        f"/sessions/{session_id}/runs",
        str(config_path),
    )
    assert int(status) == 200
    assert payload["ok"] is True
    assert payload["session"]["id"] == session_id
    assert payload["runs"][0]["run_id"] == run_id
    assert payload["runs"][0]["state"] == "completed"
    assert payload["runs"][0]["event_count"] >= 4


def test_get_run_events_returns_ordered_timeline(config_path: Path) -> None:
    session_id = "session-runs-2"

    with redirect_stdout(io.StringIO()):
        turn_status, turn_payload = dispatch_request(
            "POST",
            "/turns",
            str(config_path),
            body={"message": "event timeline", "session_id": session_id},
        )
    assert int(turn_status) == 200
    run_id = turn_payload["turn"]["run_id"]

    status, payload = dispatch_request(
        "GET",
        f"/sessions/{session_id}/runs/{run_id}/events",
        str(config_path),
    )
    assert int(status) == 200
    assert payload["ok"] is True
    assert payload["run_id"] == run_id
    states = [event["state"] for event in payload["events"]]
    assert states[0] == "queued"
    assert states[-1] == "completed"


def test_get_session_runs_invalid_limit_returns_bad_request(config_path: Path) -> None:
    status, payload = dispatch_request(
        "GET",
        "/sessions/session-runs-3/runs",
        str(config_path),
        query="limit=nope",
    )
    assert int(status) == 400
    assert payload["ok"] is False
    _assert_error_envelope(payload, code="invalid_request")


def test_get_run_events_not_found_returns_not_found(config_path: Path) -> None:
    session_id = "session-runs-4"
    with redirect_stdout(io.StringIO()):
        dispatch_request(
            "POST",
            "/turns",
            str(config_path),
            body={"message": "run", "session_id": session_id},
        )

    status, payload = dispatch_request(
        "GET",
        f"/sessions/{session_id}/runs/missing-run/events",
        str(config_path),
    )
    assert int(status) == 404
    assert payload["ok"] is False
    _assert_error_envelope(payload, code="run_not_found")


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
