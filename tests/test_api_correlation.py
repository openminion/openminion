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


def test_dispatch_generates_request_id_when_missing(config_path: Path) -> None:
    status, payload = dispatch_request("GET", "/health", str(config_path))
    assert int(status) == 200
    meta = payload.get("meta", {})
    assert isinstance(meta.get("request_id"), str)
    assert len(meta["request_id"]) == 32


def test_dispatch_uses_supplied_request_id(config_path: Path) -> None:
    status, payload = dispatch_request(
        "GET",
        "/health",
        str(config_path),
        request_id="client-req-42",
    )
    assert int(status) == 200
    assert payload["meta"]["request_id"] == "client-req-42"


def test_turn_response_meta_contains_session_id(config_path: Path) -> None:
    with redirect_stdout(io.StringIO()):
        status, payload = dispatch_request(
            "POST",
            "/turns",
            str(config_path),
            body={"message": "hello", "session_id": "corr-session"},
            request_id="turn-corr-1",
        )

    assert int(status) == 200
    assert payload["meta"]["request_id"] == "turn-corr-1"
    assert payload["meta"]["session_id"] == "corr-session"
    assert payload["turn"]["metadata"]["request_id"] == "turn-corr-1"
    assert payload["meta"]["run_id"] == payload["turn"]["run_id"]


def test_run_events_response_meta_contains_run_id_and_persisted_request_id(
    config_path: Path,
) -> None:
    session_id = "corr-session-events"
    with redirect_stdout(io.StringIO()):
        turn_status, turn_payload = dispatch_request(
            "POST",
            "/turns",
            str(config_path),
            body={"message": "hello", "session_id": session_id},
            request_id="turn-corr-events-1",
        )
    assert int(turn_status) == 200
    run_id = turn_payload["turn"]["run_id"]

    status, payload = dispatch_request(
        "GET",
        f"/sessions/{session_id}/runs/{run_id}/events",
        str(config_path),
        request_id="events-corr-1",
    )
    assert int(status) == 200
    assert payload["meta"]["request_id"] == "events-corr-1"
    assert payload["meta"]["session_id"] == session_id
    assert payload["meta"]["run_id"] == run_id
    request_ids = {
        str(item.get("payload", {}).get("request_id", ""))
        for item in payload.get("events", [])
    }
    assert "turn-corr-events-1" in request_ids


def test_logs_include_request_id(
    config_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO", logger="openminion.api"):
        dispatch_request(
            "GET",
            "/health",
            str(config_path),
            request_id="log-corr-1",
        )
    joined = "\n".join(caplog.messages)
    assert "log-corr-1" in joined
    assert "method=GET" in joined


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
