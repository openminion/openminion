from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from openminion.modules.controlplane.adapters.client import (
    OpenMinionBrainClient,
)
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

        # Replace with a mock that satisfies the OpenMinionBrainClient spec
        # so we can assert close() was invoked exactly once on shutdown.
        mock = MagicMock(spec=OpenMinionBrainClient)
        runner._brain_client = mock

        # Drive only the worker-thread lifecycle (not the polling loop,
        # which would hit the network). ``stop()`` joins the worker and
        # then calls brain_client.close().
        stop_event = threading.Event()
        runner._start_outbox_worker(stop_event)
        worker_thread = runner._outbox_thread
        assert worker_thread is not None and worker_thread.is_alive()

        stop_event.set()
        runner.stop()

        # Worker thread joined before close was invoked.
        assert not worker_thread.is_alive(), (
            "outbox worker thread did not join before stop returned"
        )
        assert mock.close.called, "brain_client.close was not invoked"
        assert mock.close.call_count == 1, mock.close.call_count
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


def test_brain_client_close_exception_is_swallowed_with_warning(
    tmp_path: Path, caplog: Any
) -> None:

    runtime = _build_runtime(tmp_path)
    try:
        runner = runtime.channels.get("telegram")
        mock = MagicMock(spec=OpenMinionBrainClient)
        mock.close.side_effect = RuntimeError("brain shutdown error")
        runner._brain_client = mock

        with caplog.at_level(logging.WARNING):
            runner.stop()  # must not raise

        assert mock.close.called
        warnings = [
            record.message
            for record in caplog.records
            if record.levelno >= logging.WARNING
            and "brain_client.close failed" in record.message
        ]
        assert warnings, [r.message for r in caplog.records]
    finally:
        _close_runtime(runtime)


def test_shutdown_ordering_outbox_join_then_brain_close(tmp_path: Path) -> None:

    runtime = _build_runtime(tmp_path)
    try:
        runner = runtime.channels.get("telegram")
        ordering: list[str] = []

        # Spy on the outbox-stop helper.
        original_stop_outbox = runner._stop_outbox_worker

        def _spy_stop_outbox(*a: Any, **kw: Any) -> Any:
            ordering.append("outbox_stop")
            return original_stop_outbox(*a, **kw)

        runner._stop_outbox_worker = _spy_stop_outbox  # type: ignore[assignment]

        # Spy on brain close.
        mock = MagicMock(spec=OpenMinionBrainClient)
        mock.close.side_effect = lambda: ordering.append("brain_close")
        runner._brain_client = mock

        runner.stop()

        assert ordering == ["outbox_stop", "brain_close"], ordering
    finally:
        _close_runtime(runtime)
