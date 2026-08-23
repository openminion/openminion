from openminion.base.config.parser.runtime import (
    _build_runtime_config,
    _runtime_config_to_payload,
)


def test_provider_retry_max_attempts_round_trips() -> None:
    config = _build_runtime_config({"provider_retry_max_attempts": 1})

    assert config.provider_retry_max_attempts == 1
    assert _runtime_config_to_payload(config)["provider_retry_max_attempts"] == 1
