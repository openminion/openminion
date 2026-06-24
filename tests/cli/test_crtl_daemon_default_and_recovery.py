from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from openminion.cli.commands import daemon as daemon_module
from openminion.cli.chat.runtime import _resolve_daemon_auto_start


# 1. Default auto-start


def test_default_runtime_config_now_auto_starts_daemon():

    from openminion.base.config.runtime import RuntimeConfig

    cfg = RuntimeConfig()
    assert cfg.daemon_auto_start is True


def test_resolve_daemon_auto_start_returns_true_when_config_true(monkeypatch):
    monkeypatch.delenv("OPENMINION_DAEMON_AUTO_START", raising=False)
    cfg = SimpleNamespace(runtime=SimpleNamespace(daemon_auto_start=True))
    assert _resolve_daemon_auto_start(cfg) is True


# 2. Env opt-out


def test_env_opt_out_overrides_config_true(monkeypatch):

    monkeypatch.setenv("OPENMINION_DAEMON_AUTO_START", "0")
    cfg = SimpleNamespace(runtime=SimpleNamespace(daemon_auto_start=True))
    assert _resolve_daemon_auto_start(cfg) is False


def test_env_truthy_forces_auto_start_even_when_config_false(monkeypatch):

    monkeypatch.setenv("OPENMINION_DAEMON_AUTO_START", "1")
    cfg = SimpleNamespace(runtime=SimpleNamespace(daemon_auto_start=False))
    assert _resolve_daemon_auto_start(cfg) is True


def test_env_falsy_values_recognized(monkeypatch):

    for val in ("0", "false", "no", "off", "FALSE", "Off"):
        monkeypatch.setenv("OPENMINION_DAEMON_AUTO_START", val)
        cfg = SimpleNamespace(runtime=SimpleNamespace(daemon_auto_start=True))
        assert _resolve_daemon_auto_start(cfg) is False, f"failed for {val!r}"


def test_env_unset_falls_back_to_config(monkeypatch):

    monkeypatch.delenv("OPENMINION_DAEMON_AUTO_START", raising=False)
    cfg_false = SimpleNamespace(runtime=SimpleNamespace(daemon_auto_start=False))
    cfg_true = SimpleNamespace(runtime=SimpleNamespace(daemon_auto_start=True))
    assert _resolve_daemon_auto_start(cfg_false) is False
    assert _resolve_daemon_auto_start(cfg_true) is True


# 3. Auto-start failure produces the one-line fallback notice


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


def test_one_line_fallback_notice_emits_on_runtime_error(capsys):

    from openminion.cli.chat.ui import print_fallback_notice

    print_fallback_notice(RuntimeError("daemon-start failed: port in use"))
    out = capsys.readouterr().out
    assert "[chat] daemon unavailable, falling back to in-process runtime" in out
    assert "port in use" in out


# 4. Config-mismatch recovery text


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


# Single-process mode opt-out preserved (spec guardrail #7)


def test_single_process_mode_skips_ensure_daemon_running_entirely(monkeypatch):

    from openminion.cli.chat.runtime import init_runtime_state

    monkeypatch.delenv("OPENMINION_DAEMON_AUTO_START", raising=False)
    config = SimpleNamespace(
        runtime=SimpleNamespace(
            process_mode="single-process",
            daemon_auto_start=True,
        )
    )
    args = SimpleNamespace(
        config="/x",
        quiet=True,
        no_progress=True,
        no_activity_indicator=True,
        home_root=None,
        data_root=None,
    )

    # If ensure_daemon_running were called, this would fail with
    # MagicMock attribute errors. We assert it's not called.
    with mock.patch("openminion.cli.chat.runtime.ensure_daemon_running") as mock_ensure:
        state, err = init_runtime_state(args, config)

    mock_ensure.assert_not_called()
    assert state.mode == "single-process"
    assert state.transport == "in-process"
    assert err is None
