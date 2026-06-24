from __future__ import annotations

from pathlib import Path

import pytest

from openminion.tools.browser.providers.pinchtab import daemon as daemon_mod
from openminion.tools.browser.providers.pinchtab.daemon import (
    PinchTabDaemonConfig,
    start_daemon,
)


class _FakeProcess:
    def __init__(self, pid: int = 12345) -> None:
        self.pid = pid


class _HandleTracker:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.close_called = False

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self) -> None:
        self.close_called = True
        self._inner.close()

    @property
    def closed(self) -> bool:
        return bool(self._inner.closed)


@pytest.fixture
def _cfg(tmp_path) -> PinchTabDaemonConfig:
    runtime_dir = tmp_path / "rt"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return PinchTabDaemonConfig(
        base_url="http://127.0.0.1:9999",
        runtime_dir=runtime_dir,
        launch_cmd=("/bin/true",),
        launch_timeout_s=5,
        env={},
    )


def _install_handle_tracker(
    monkeypatch, cfg: PinchTabDaemonConfig
) -> list[_HandleTracker]:
    trackers: list[_HandleTracker] = []
    original_open = Path.open

    def tracked_open(self, *args, **kwargs):
        inner = original_open(self, *args, **kwargs)
        if self == cfg.log_file:
            tracker = _HandleTracker(inner)
            trackers.append(tracker)
            return tracker
        return inner

    monkeypatch.setattr(Path, "open", tracked_open)
    return trackers


def test_log_handle_closed_after_popen_success(monkeypatch, _cfg):
    trackers = _install_handle_tracker(monkeypatch, _cfg)

    def fake_popen(*args, **kwargs):
        # Sanity: the log_handle must still be open when Popen is invoked,
        # since the child needs it for dup. Closing happens AFTER Popen
        # returns (the `finally` block).
        assert trackers, "log_handle should have been opened before Popen"
        assert not trackers[-1].closed
        return _FakeProcess(pid=4242)

    monkeypatch.setattr(daemon_mod.subprocess, "Popen", fake_popen)

    result = start_daemon(_cfg)

    assert result == {
        "started": True,
        "pid": 4242,
        "status": daemon_mod.daemon_status(_cfg),
    }
    assert trackers, "expected log_handle to be opened by start_daemon"
    assert trackers[-1].close_called, "log_handle must be explicitly closed"
    assert trackers[-1].closed, "log_handle must be closed after start_daemon returns"


def test_log_handle_closed_after_popen_failure(monkeypatch, _cfg):
    trackers = _install_handle_tracker(monkeypatch, _cfg)

    def fake_popen(*args, **kwargs):
        raise RuntimeError("simulated launch failure")

    monkeypatch.setattr(daemon_mod.subprocess, "Popen", fake_popen)

    with pytest.raises(RuntimeError, match="simulated launch failure"):
        start_daemon(_cfg)

    assert trackers, "expected log_handle to be opened by start_daemon"
    assert trackers[-1].close_called
    assert trackers[-1].closed, "log_handle must be closed even when Popen raises"
