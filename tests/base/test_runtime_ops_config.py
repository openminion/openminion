from __future__ import annotations

import pytest

from openminion.base.config.base import ConfigError
from openminion.base.config.parser.runtime import (
    _build_runtime_config,
    _runtime_config_to_payload,
)


def test_ops_config_round_trips_without_exposing_secrets() -> None:
    raw = {
        "targets": [
            {
                "target_id": "local-dev",
                "transport": "local",
                "credential_ref": "env:OPS_PASSWORD",
            }
        ]
    }

    config = _build_runtime_config({"ops": raw})

    assert config.ops == raw
    assert _runtime_config_to_payload(config)["ops"] == raw


def test_ops_config_requires_an_object() -> None:
    with pytest.raises(ConfigError, match="runtime.ops.*object"):
        _build_runtime_config({"ops": []})
