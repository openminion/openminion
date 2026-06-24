from __future__ import annotations

from openminion.modules.telemetry.adapters.logfire import (
    LOGFIRE_OTLP_ENDPOINT,
    build_logfire_otel_config,
)


def test_logfire_adapter_no_op_when_token_missing() -> None:
    # Pass an empty env dict so the test does not depend on host env vars.
    result = build_logfire_otel_config(env={})
    assert result is None


def test_logfire_adapter_no_op_does_not_set_headers_envvar() -> None:
    env: dict[str, str] = {}
    build_logfire_otel_config(env=env)
    assert "OTEL_EXPORTER_OTLP_HEADERS" not in env


def test_logfire_adapter_builds_config_from_token_env() -> None:
    env = {"LOGFIRE_TOKEN": "lf_test_token_xyz"}
    config = build_logfire_otel_config(env=env)
    assert config is not None
    assert config.enabled is True
    assert config.endpoint == LOGFIRE_OTLP_ENDPOINT
    assert config.protocol == "http"
    assert config.service_name == "openminion"
    assert env["OTEL_EXPORTER_OTLP_HEADERS"] == (
        "Authorization=Bearer lf_test_token_xyz"
    )


def test_logfire_adapter_explicit_token_overrides_env() -> None:
    env = {"LOGFIRE_TOKEN": "from_env"}
    config = build_logfire_otel_config(token="from_arg", env=env)
    assert config is not None
    assert env["OTEL_EXPORTER_OTLP_HEADERS"] == "Authorization=Bearer from_arg"


def test_logfire_adapter_service_name_propagates() -> None:
    config = build_logfire_otel_config(
        token="t",
        service_name="my-agent",
        env={},
    )
    assert config is not None
    assert config.service_name == "my-agent"
