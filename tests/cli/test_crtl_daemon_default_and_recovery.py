from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from openminion.cli.commands import daemon as daemon_module
def test_default_runtime_config_now_auto_starts_daemon():

    from openminion.base.config.runtime import RuntimeConfig

    cfg = RuntimeConfig()
    assert cfg.daemon_auto_start is True

def test_ensure_daemon_running_raises_when_auto_start_disabled():

    fake_endpoint = SimpleNamespace(host="127.0.0.1", port=18789, config_path="/x")
    with (
        mock.patch.object(
            daemon_module, "resolve_daemon_endpoint", return_value=fake_endpoint
        ),
        mock.patch.object(
            daemon_module, "probe_daemon_endpoint", return_value=("unreachable", {})
        ),
    ):
        with pytest.raises(RuntimeError, match="daemon is not running"):
            daemon_module.ensure_daemon_running("/x", auto_start=False)


def test_ensure_daemon_running_surfaces_start_failure_message():

    fake_endpoint = SimpleNamespace(host="127.0.0.1", port=18789, config_path="/x")
    with (
        mock.patch.object(
            daemon_module, "resolve_daemon_endpoint", return_value=fake_endpoint
        ),
        mock.patch.object(
            daemon_module, "probe_daemon_endpoint", return_value=("unreachable", {})
        ),
        mock.patch.object(
            daemon_module,
            "_start_daemon",
            return_value={"ok": False, "message": "daemon-start failed: port in use"},
        ),
    ):
        with pytest.raises(RuntimeError, match="port in use"):
            daemon_module.ensure_daemon_running("/x", auto_start=True)


def test_config_mismatch_error_contains_three_recovery_commands():

    fake_endpoint = SimpleNamespace(host="127.0.0.1", port=18789, config_path="/cfg-A")
    mismatch_payload = {"daemon": {"config_path": "/cfg-B"}}
    with (
        mock.patch.object(
            daemon_module, "resolve_daemon_endpoint", return_value=fake_endpoint
        ),
        mock.patch.object(
            daemon_module,
            "probe_daemon_endpoint",
            return_value=(daemon_module._PROBE_STATUS_MISMATCH, mismatch_payload),
        ),
    ):
        with pytest.raises(RuntimeError) as ei:
            daemon_module.ensure_daemon_running("/cfg-A", auto_start=True)

        msg = str(ei.value)
        assert "openminion daemon stop" in msg
        assert "--session" in msg
        assert "--reset-session" in msg
        # Plus the diagnostic content (which config was occupying).
        assert "/cfg-A" in msg
        assert "/cfg-B" in msg


def test_config_mismatch_error_handles_missing_remote_config_payload():

    fake_endpoint = SimpleNamespace(host="127.0.0.1", port=18789, config_path="/cfg-A")
    with (
        mock.patch.object(
            daemon_module, "resolve_daemon_endpoint", return_value=fake_endpoint
        ),
        mock.patch.object(
            daemon_module,
            "probe_daemon_endpoint",
            return_value=(daemon_module._PROBE_STATUS_MISMATCH, {}),
        ),
    ):
        with pytest.raises(RuntimeError) as ei:
            daemon_module.ensure_daemon_running("/cfg-A", auto_start=True)

        msg = str(ei.value)
        assert "unknown" in msg
        assert "openminion daemon stop" in msg
