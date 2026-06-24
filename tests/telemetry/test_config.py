from __future__ import annotations

import json

from openminion.modules.telemetry.config import load_config


def test_load_config_reads_runtime_telemetry_exporter_from_mapping() -> None:
    config = load_config(
        {
            "runtime": {
                "telemetry_exporter": {
                    "enabled": True,
                    "endpoint": "http://collector:4318",
                    "protocol": "grpc",
                    "service_name": "telemetry-test",
                    "sample_rate": 0.25,
                    "include_assistant_body": True,
                }
            }
        }
    )

    assert config.export.oteler.enabled is True
    assert config.export.oteler.endpoint == "http://collector:4318"
    assert config.export.oteler.protocol == "grpc"
    assert config.export.oteler.service_name == "telemetry-test"
    assert config.export.oteler.sample_rate == 0.25
    assert config.export.oteler.include_assistant_body is True


def test_load_config_reads_runtime_telemetry_exporter_from_path(tmp_path) -> None:
    config_path = tmp_path / "openminion.json"
    config_path.write_text(
        json.dumps(
            {
                "runtime": {
                    "telemetry_exporter": {
                        "enabled": True,
                        "endpoint": "http://collector:4318",
                        "service_name": "from-file",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.export.oteler.enabled is True
    assert config.export.oteler.endpoint == "http://collector:4318"
    assert config.export.oteler.service_name == "from-file"
