import os
from unittest import mock

from tests._csc_fixtures import _csc_install_default_agent

from openminion.api.server import build_api_server, dispatch_request
from openminion.base.config import OpenMinionConfig, save_config


def _dispatch_degraded(tmp_path, method: str, route: str, *, body=None):
    config_path = _write_openai_missing_key_config(tmp_path)
    return dispatch_request(
        method,
        route,
        str(config_path),
        body=body,
        runtime_bootstrap_error="OpenAI provider selected but API key is missing.",
    )


def test_health_reports_degraded_when_startup_bootstrap_failed(tmp_path) -> None:
    status, payload = _dispatch_degraded(tmp_path, "GET", "/health")

    assert int(status) == 503
    assert payload.get("degraded")
    assert "API key is missing" in payload.get("degraded_reason", "")
    recovery = payload.get("degraded_recovery", {})
    assert recovery.get("configured_provider") == "openai"
    assert "recommended_actions" in recovery
    assert "OPENAI_API_KEY" in "\n".join(recovery.get("recommended_actions", []))


def test_turns_returns_service_unavailable_in_degraded_mode(tmp_path) -> None:
    status, payload = _dispatch_degraded(
        tmp_path, "POST", "/turns", body={"message": "hello"}
    )

    assert int(status) == 503
    assert not payload["ok"]
    assert payload["error"]["code"] == "runtime_unavailable"
    assert payload["error"]["retryable"]
    assert payload["error"]["retry_after_ms"] == 1000
    assert "bootstrap_error" in payload["error"]["details"]
    assert payload["error"]["details"]["recovery_path"] == "/health"
    assert "recommendation" in payload["error"]["details"]


def test_metrics_route_remains_available_in_degraded_mode(tmp_path) -> None:
    status, payload = _dispatch_degraded(tmp_path, "GET", "/metrics")

    assert int(status) == 200
    assert payload["ok"]
    assert "metrics" in payload


def test_sessions_returns_service_unavailable_in_degraded_mode(tmp_path) -> None:
    status, payload = _dispatch_degraded(tmp_path, "GET", "/sessions/example/messages")

    assert int(status) == 503
    assert payload["error"]["code"] == "runtime_unavailable"


def test_build_api_server_starts_with_none_runtime_when_bootstrap_fails() -> None:
    fake_server = mock.Mock()
    fake_server.server_address = ("127.0.0.1", 0)

    with mock.patch(
        "openminion.api.server.APIRuntime.from_config_path",
        side_effect=RuntimeError("bootstrap failed"),
    ):
        with mock.patch(
            "openminion.api.server._OpenMinionThreadingHTTPServer",
            return_value=fake_server,
        ) as server_ctor:
            server = build_api_server(
                config_path="config.json", host="127.0.0.1", port=0
            )

    assert server is fake_server
    assert server_ctor.call_args.kwargs["runtime"] is None
    handler_cls = server_ctor.call_args.args[1]
    assert getattr(handler_cls, "runtime") is None
    assert "bootstrap failed" in getattr(handler_cls, "runtime_bootstrap_error")


def _write_openai_missing_key_config(tmp_path):
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    # Set OPENMINION_DATA_ROOT to tmp for test isolation
    os.environ["OPENMINION_DATA_ROOT"] = str(tmp_path / ".openminion")
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="openai")
    config.providers.openai.api_key = ""
    config.providers.openai.api_key_env = "OPENMINION_TEST_OPENAI_KEY_MISSING"
    config.storage.path = str(tmp_path / "state" / "api.db")
    save_config(config, str(config_path))
    return config_path
