from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)
from openminion.services.runtime.lifecycle import LifecycleService
from openminion.services.security.policy import SecurityPolicyEngine

from tests.integration.test_unified_config_bootstrap import (
    _close_runtime,
    _make_config,
)


def _build_runtime(tmp_path: Path):
    config = _make_config(tmp_path, mode="polling")
    telegram = config.channels["telegram"]
    telegram["pairing"] = {"enabled": False, "mode": "off"}

    lifecycle = LifecycleService.from_config(
        config,
        config_path=str(tmp_path / "config.json"),
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
    )
    runtime = lifecycle.build(
        security_policy=SecurityPolicyEngine(),
        load_tool_plugins=False,
    )
    return runtime


def test_brain_client_close_invoked_on_runner_stop(tmp_path: Path) -> None:

    runtime = _build_runtime(tmp_path)
    try:
        runner = runtime.channels.get("telegram")
        assert isinstance(runner, TelegramPollingRunner)
        # Lifecycle wired EchoBrain (openminion_enabled=False); the runner
        # holds it as ``_brain_client``. Verify the slot is wired.
        assert runner._brain_client is not None, (
            "lifecycle did not pass brain_client into the runner"
        )

        supervisor = runtime.channel_supervisor
        assert supervisor is not None, "lifecycle did not wire channel supervisor"

        close_runtime = MagicMock()
        supervisor._close_runtime = close_runtime

        supervisor.stop()

        assert close_runtime.call_count == 1, close_runtime.call_count
    finally:
        _close_runtime(runtime)


def test_brain_client_without_close_method_does_not_crash(
    tmp_path: Path,
) -> None:

    runtime = _build_runtime(tmp_path)
    try:
        runner = runtime.channels.get("telegram")
        # Confirm the brain attached has no ``close`` (EchoBrain shape).
        assert runner._brain_client is not None
        assert not hasattr(runner._brain_client, "close"), (
            "EchoBrain unexpectedly grew a close method; revisit the test"
        )

        # Should not raise.
        runner.stop()
    finally:
        _close_runtime(runtime)


def test_brain_client_close_exception_is_swallowed(tmp_path: Path) -> None:

    runtime = _build_runtime(tmp_path)
    try:
        supervisor = runtime.channel_supervisor
        assert supervisor is not None, "lifecycle did not wire channel supervisor"
        supervisor._close_runtime = MagicMock(
            side_effect=RuntimeError("brain shutdown error")
        )

        supervisor.stop()  # must not raise

        assert supervisor.status().last_error == "brain shutdown error"
    finally:
        _close_runtime(runtime)


def test_shutdown_ordering_outbox_join_then_brain_close(tmp_path: Path) -> None:

    runtime = _build_runtime(tmp_path)
    try:
        supervisor = runtime.channel_supervisor
        assert supervisor is not None, "lifecycle did not wire channel supervisor"
        ordering: list[str] = []

        original_stop_outbox = supervisor._stop_outbox_worker

        def _spy_stop_outbox(*a: Any, **kw: Any) -> Any:
            ordering.append("outbox_stop")
            return original_stop_outbox(*a, **kw)

        supervisor._stop_outbox_worker = _spy_stop_outbox  # type: ignore[assignment]

        supervisor._close_runtime = lambda: ordering.append("runtime_close")

        supervisor.stop()

        assert ordering == ["outbox_stop", "runtime_close"], ordering
    finally:
        _close_runtime(runtime)
