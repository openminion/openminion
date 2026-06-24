from http import HTTPStatus
from pathlib import Path

import pytest

from tests._csc_fixtures import _csc_install_default_agent

from openminion.api.responses.serialization import error_response
from openminion.api.server import dispatch_request, parse_json_request_body
from openminion.base.config import OpenMinionConfig, save_config


def test_parse_json_request_body_valid() -> None:
    payload = parse_json_request_body(
        content_length_raw="16", raw_body='{"ok":"value"}   '
    )
    assert payload["ok"] == "value"


def test_parse_json_request_body_invalid_content_length() -> None:
    with pytest.raises(ValueError, match="Invalid Content-Length"):
        parse_json_request_body(content_length_raw="nope", raw_body='{"x":1}')


def test_parse_json_request_body_missing_body() -> None:
    with pytest.raises(ValueError, match="JSON request body is required"):
        parse_json_request_body(content_length_raw="0", raw_body="")


def test_parse_json_request_body_shorter_than_content_length() -> None:
    with pytest.raises(ValueError, match="shorter than Content-Length"):
        parse_json_request_body(content_length_raw="100", raw_body='{"x":1}')


def test_parse_json_request_body_invalid_json() -> None:
    with pytest.raises(ValueError, match="must be valid JSON"):
        parse_json_request_body(content_length_raw="5", raw_body="{bad}")


def test_parse_json_request_body_non_object() -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        parse_json_request_body(content_length_raw="3", raw_body="123")


def test_dispatch_unknown_method_returns_not_found_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _write_echo_config(tmp_path, monkeypatch)
    status, payload = dispatch_request("PUT", "/health", str(config_path))
    assert int(status) == 404
    assert payload["ok"] is False
    assert payload["error"]["code"] == "not_found"
    assert "retryable" in payload["error"]
    assert "retry_after_ms" in payload["error"]


def test_error_response_normalizes_singular_detail_mapping() -> None:
    status, payload = error_response(
        HTTPStatus.BAD_REQUEST,
        error={
            "code": "invalid_request",
            "message": "bad request",
            "detail": {"path": "/turns"},
        },
    )
    assert int(status) == 400
    assert payload["error"]["details"] == {"path": "/turns"}
    assert "detail" not in payload["error"]


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
