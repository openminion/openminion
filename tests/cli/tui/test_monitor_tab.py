from __future__ import annotations

import pytest

from openminion.cli.tui.tabs.monitor import (
    MonitorTab,
    _bar,
    _collect_metrics,
    _human_bytes,
)


def test_bar_renders_correct_width() -> None:
    assert len(_bar(0, 20)) == 20
    assert len(_bar(100, 20)) == 20
    assert len(_bar(50, 20)) == 20


def test_bar_fill_proportional() -> None:
    result = _bar(50, 20)
    assert result.count("█") == 10
    assert result.count("░") == 10


def test_bar_zero_percent() -> None:
    assert _bar(0, 10) == "░" * 10


def test_bar_full_percent() -> None:
    assert _bar(100, 10) == "█" * 10


def test_bar_clamps_over_100() -> None:
    result = _bar(150, 10)
    assert result.count("█") == 10


def test_human_bytes_units() -> None:
    assert _human_bytes(512) == "512.0 B"
    assert _human_bytes(1024) == "1.0 KB"
    assert _human_bytes(1024 * 1024) == "1.0 MB"
    assert _human_bytes(1024**3) == "1.0 GB"
    assert _human_bytes(1024**4) == "1.0 TB"


def test_collect_metrics_returns_expected_keys() -> None:
    data = _collect_metrics()
    assert "available" in data
    assert "process" in data
    assert "pid" in data["process"]
    assert "network" in data
    assert "net_sent_per_s" in data["network"]
    assert "net_recv_per_s" in data["network"]
    if data["available"]:
        assert "cpu" in data
        assert "memory" in data
        assert "disk" in data
        assert "percent" in data["cpu"]
        assert "percent" in data["memory"]
        assert "percent" in data["disk"]
        assert "cpu_percent" in data["process"]


def test_collect_metrics_cpu_percent_is_number() -> None:
    data = _collect_metrics()
    if data["available"]:
        assert isinstance(data["cpu"]["percent"], (int, float))
        assert 0 <= data["cpu"]["percent"] <= 100


def test_collect_metrics_memory_percent_is_number() -> None:
    data = _collect_metrics()
    if data["available"]:
        assert isinstance(data["memory"]["percent"], (int, float))
        assert 0 <= data["memory"]["percent"] <= 100


@pytest.mark.asyncio
async def test_monitor_tab_mounts() -> None:
    from openminion.cli.tui.app import OpenMinionApp

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        monitor = app.screen.query_one(MonitorTab)
        assert monitor is not None
