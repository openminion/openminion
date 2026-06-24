from __future__ import annotations

from openminion.base.config.parser.runtime import (
    _build_runtime_config,
    _runtime_config_to_payload,
)


def test_runtime_telemetry_exporter_config_parses_and_round_trips() -> None:
    config = _build_runtime_config(
        {
            "telemetry_exporter": {
                "enabled": True,
                "endpoint": "http://collector:4318",
                "service_name": "openminion-prod",
                "protocol": "grpc",
                "include_assistant_body": True,
                "sample_rate": 0.25,
            }
        }
    )

    assert config.telemetry_exporter.enabled is True
    assert config.telemetry_exporter.endpoint == "http://collector:4318"
    assert config.telemetry_exporter.service_name == "openminion-prod"
    assert config.telemetry_exporter.protocol == "grpc"
    assert config.telemetry_exporter.include_assistant_body is True
    assert config.telemetry_exporter.sample_rate == 0.25

    payload = _runtime_config_to_payload(config)
    assert payload["telemetry_exporter"] == {
        "enabled": True,
        "endpoint": "http://collector:4318",
        "service_name": "openminion-prod",
        "protocol": "grpc",
        "include_assistant_body": True,
        "sample_rate": 0.25,
        # backend + headers default to empty when the input config
        # does not set them; round-trip preserves the empty shape.
        "backend": "",
        "headers": {},
    }


def test_runtime_telemetry_exporter_config_parses_backend_and_headers() -> None:
    config = _build_runtime_config(
        {
            "telemetry_exporter": {
                "enabled": True,
                "endpoint": "https://us.cloud.langfuse.com/api/public/otel",
                "backend": "langfuse",
                "headers": {
                    "Authorization": "Basic redacted",
                    "x-source": "openminion",
                },
            }
        }
    )

    assert config.telemetry_exporter.backend == "langfuse"
    assert config.telemetry_exporter.headers == {
        "Authorization": "Basic redacted",
        "x-source": "openminion",
    }

    payload = _runtime_config_to_payload(config)
    assert payload["telemetry_exporter"]["backend"] == "langfuse"
    assert payload["telemetry_exporter"]["headers"] == {
        "Authorization": "Basic redacted",
        "x-source": "openminion",
    }


def test_runtime_telemetry_exporter_config_rejects_non_dict_headers() -> None:
    config = _build_runtime_config(
        {
            "telemetry_exporter": {
                "enabled": True,
                "endpoint": "http://collector:4318",
                "headers": "Bearer redacted",  # wrong shape
            }
        }
    )

    assert config.telemetry_exporter.headers == {}
