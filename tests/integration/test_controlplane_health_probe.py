from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from openminion.modules.controlplane.runtime.health_probe import (
    ControlPlaneHealthProbeConfig,
    ControlPlaneHealthProbeSidecar,
)


def _get_json(url: str, *, token: str | None = None) -> tuple[int, dict[str, object]]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with urlopen(Request(url, headers=headers), timeout=2) as response:  # noqa: S310
        return response.status, json.loads(response.read().decode("utf-8"))


def test_health_probe_serves_health_ready_status_and_metrics() -> None:
    sidecar = ControlPlaneHealthProbeSidecar(
        config=ControlPlaneHealthProbeConfig(port=0),
        get_status=lambda: {
            "channel_runtime": {"state": "running", "channels": {}},
            "chat_key": "secret-chat",
            "telegram": {
                "bot_token": "secret-token",
                "webhook_secret": "secret-webhook",
            },
        },
        get_audit_health=lambda: {"audit": {"healthy": True, "failures": 0}},
        probe_store=lambda: True,
        get_metrics=lambda: b"controlplane_inbound_total 1\n",
    )
    sidecar.start()
    port = int(sidecar.status()["port"])
    try:
        assert _get_json(f"http://127.0.0.1:{port}/healthz")[1] == {"ok": True}
        assert _get_json(f"http://127.0.0.1:{port}/readyz")[0] == 200
        status = _get_json(f"http://127.0.0.1:{port}/status")[1]
        assert status["chat_key"] == "redacted"
        telegram = status["telegram"]
        assert isinstance(telegram, dict)
        assert telegram["bot_token"] == "redacted"
        assert telegram["webhook_secret"] == "redacted"
        with urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2) as response:  # noqa: S310
            assert response.read() == b"controlplane_inbound_total 1\n"
    finally:
        sidecar.stop(kill=True)


def test_health_probe_readiness_degrades_when_audit_or_store_fails() -> None:
    sidecar = ControlPlaneHealthProbeSidecar(
        config=ControlPlaneHealthProbeConfig(port=0),
        get_audit_health=lambda: {"audit": {"healthy": False, "failures": 1}},
        probe_store=lambda: False,
    )
    sidecar.start()
    port = int(sidecar.status()["port"])
    try:
        with pytest.raises(HTTPError) as excinfo:
            _get_json(f"http://127.0.0.1:{port}/readyz")
        assert excinfo.value.code == 503
    finally:
        sidecar.stop(kill=True)


def test_health_probe_refuses_remote_bind_without_auth() -> None:
    sidecar = ControlPlaneHealthProbeSidecar(
        config=ControlPlaneHealthProbeConfig(host="0.0.0.0", port=0)
    )
    with pytest.raises(ValueError, match="remote bind requires"):
        sidecar.start()
