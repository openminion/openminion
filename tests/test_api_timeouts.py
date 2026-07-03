import asyncio
import os
from pathlib import Path
from unittest import mock

import pytest

from tests._csc_fixtures import _csc_install_default_agent

from openminion.api.server import dispatch_request
from openminion.base.config import OpenMinionConfig, save_config
from openminion.api.runtime import APIRuntime
from openminion.api.turns import TurnTimeoutError, run_turn
from openminion.services.lifecycle.request_orchestrator import _resolve_timeout_seconds


def _cortensor_timeout_config() -> OpenMinionConfig:
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="cortensor")
    config.gateway.api_turn_timeout_seconds = 45
    config.providers.cortensor.timeout_seconds = 60
    config.providers.cortensor.precommit_timeout_seconds = 120
    config.providers.cortensor.transport_timeout_buffer_seconds = 10
    return config


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"message": "hello"}, 135.0),
        ({"message": "hello", "timeout_seconds": 30}, 30.0),
    ],
)
def test_resolve_timeout_seconds(payload: dict[str, object], expected: float) -> None:
    config = _cortensor_timeout_config()

    resolved = _resolve_timeout_seconds(
        payload=payload,
        default_seconds=config.gateway.api_turn_timeout_seconds,
        config=config,
    )

    assert resolved == expected


def test_run_turn_timeout_raises(tmp_path) -> None:
    config_path = _write_echo_config(tmp_path)
    runtime = APIRuntime.from_config_path(str(config_path))
    try:

        async def _slow_run_once(
            *, channel, target, message, session_id=None, idempotency_key=None
        ):
            del channel, target, message, session_id, idempotency_key
            await asyncio.sleep(0.05)
            raise RuntimeError("should timeout before this")

        runtime.gateway.run_once = _slow_run_once  # type: ignore[assignment]
        with pytest.raises(TurnTimeoutError):
            run_turn(
                str(config_path),
                {
                    "message": "hello",
                    "timeout_seconds": 0.01,
                },
                runtime=runtime,
            )
    finally:
        runtime.close()


def test_dispatch_turn_timeout_maps_504(tmp_path) -> None:
    config_path = _write_echo_config(tmp_path)
    with mock.patch(
        "openminion.api.server.run_turn",
        side_effect=TurnTimeoutError("timed out"),
    ):
        status, payload = dispatch_request(
            "POST",
            "/turns",
            str(config_path),
            body={"message": "hello"},
            request_id="timeout-map-1",
        )

    assert int(status) == 504
    assert payload["ok"] is False
    assert payload["error"]["code"] == "turn_timeout"
    assert payload["error"]["retryable"] is True
    assert payload["error"]["retry_after_ms"] == 1000
    assert payload["meta"]["request_id"] == "timeout-map-1"


def test_dispatch_invalid_timeout_returns_bad_request(tmp_path) -> None:
    config_path = _write_echo_config(tmp_path)
    status, payload = dispatch_request(
        "POST",
        "/turns",
        str(config_path),
        body={"message": "hello", "timeout_seconds": 0},
    )

    assert int(status) == 400
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_request"


def _write_echo_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    os.environ["OPENMINION_DATA_ROOT"] = str(tmp_path / ".openminion")
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "api.db")
    save_config(config, str(config_path))
    return config_path
