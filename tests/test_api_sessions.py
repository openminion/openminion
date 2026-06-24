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


def test_get_session_messages_returns_transcript(config_path: Path) -> None:
    session_id = "session-api-1"
    with redirect_stdout(io.StringIO()):
        dispatch_request(
            "POST",
            "/turns",
            str(config_path),
            body={"message": "first", "session_id": session_id},
        )
        dispatch_request(
            "POST",
            "/turns",
            str(config_path),
            body={"message": "second", "session_id": session_id},
        )

    status, payload = dispatch_request(
        "GET",
        f"/sessions/{session_id}/messages",
        str(config_path),
    )
    assert int(status) == 200
    assert payload["ok"] is True
    assert payload["session"]["id"] == session_id
    assert len(payload["messages"]) == 4
    assert payload["messages"][0]["role"] == "inbound"
    assert payload["messages"][1]["role"] == "outbound"


def test_get_session_messages_limit_query(config_path: Path) -> None:
    session_id = "session-api-2"
    with redirect_stdout(io.StringIO()):
        dispatch_request(
            "POST",
            "/turns",
            str(config_path),
            body={"message": "first", "session_id": session_id},
        )

    status, payload = dispatch_request(
        "GET",
        f"/sessions/{session_id}/messages",
        str(config_path),
        query="limit=1",
    )
    assert int(status) == 200
    assert payload["ok"] is True
    assert payload["limit"] == 1
    assert len(payload["messages"]) == 1


def test_get_session_messages_not_found(config_path: Path) -> None:
    status, payload = dispatch_request(
        "GET",
        "/sessions/missing/messages",
        str(config_path),
    )
    assert int(status) == 404
    assert payload["ok"] is False
    _assert_error_envelope(payload, code="session_not_found")


def test_get_session_messages_invalid_limit(config_path: Path) -> None:
    status, payload = dispatch_request(
        "GET",
        "/sessions/any/messages",
        str(config_path),
        query="limit=bad",
    )
    assert int(status) == 400
    assert payload["ok"] is False
    _assert_error_envelope(payload, code="invalid_request")


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
