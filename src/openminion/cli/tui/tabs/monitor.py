from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Label, LoadingIndicator, Static

_PROCESS_START = time.monotonic()
_BAR_WIDTH = 20
_LAST_NET_SAMPLE: tuple[float, float, float] | None = None


def _bar(pct: float, width: int = _BAR_WIDTH) -> str:
    filled = max(0, min(width, int(round(pct / 100 * width))))
    return "█" * filled + "░" * (width - filled)


def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _uptime_text(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    h = s // 3600
    m = (s % 3600) // 60
    return f"{h}h {m}m"


def _collect_metrics() -> dict[str, Any]:
    global _LAST_NET_SAMPLE

    data: dict[str, Any] = {}
    try:
        import psutil

        cpu_pct = psutil.cpu_percent(interval=0)
        cpu_count = psutil.cpu_count() or 1
        try:
            load = os.getloadavg()
            load_str = f"{load[0]:.1f}, {load[1]:.1f}, {load[2]:.1f}"
        except OSError:
            load_str = "—"
        data["cpu"] = {
            "percent": cpu_pct,
            "cores": cpu_count,
            "load_avg": load_str,
        }

        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        data["memory"] = {
            "percent": mem.percent,
            "used": _human_bytes(mem.used),
            "total": _human_bytes(mem.total),
            "swap_used": _human_bytes(swap.used),
            "swap_total": _human_bytes(swap.total),
        }

        try:
            disk = psutil.disk_usage("/")
        except OSError:
            disk = psutil.disk_usage(os.path.expanduser("~"))
        data["disk"] = {
            "percent": disk.percent,
            "used": _human_bytes(disk.used),
            "total": _human_bytes(disk.total),
            "free": _human_bytes(disk.free),
        }

        proc = psutil.Process(os.getpid())
        mem_info = proc.memory_info()
        proc_cpu_pct = proc.cpu_percent(interval=0)
        data["process"] = {
            "pid": os.getpid(),
            "rss": _human_bytes(mem_info.rss),
            "rss_bytes": float(mem_info.rss),
            "threads": proc.num_threads(),
            "uptime": _uptime_text(time.monotonic() - _PROCESS_START),
            "cpu_percent": proc_cpu_pct,
        }

        net = psutil.net_io_counters()
        now = time.monotonic()
        net_sent_per_s = 0.0
        net_recv_per_s = 0.0
        if _LAST_NET_SAMPLE is not None:
            prev_sent, prev_recv, prev_time = _LAST_NET_SAMPLE
            elapsed = max(0.001, now - prev_time)
            net_sent_per_s = max(0.0, (float(net.bytes_sent) - prev_sent) / elapsed)
            net_recv_per_s = max(0.0, (float(net.bytes_recv) - prev_recv) / elapsed)
        _LAST_NET_SAMPLE = (float(net.bytes_sent), float(net.bytes_recv), now)
        data["network"] = {
            "net_sent_per_s": net_sent_per_s,
            "net_recv_per_s": net_recv_per_s,
            "summary": (
                f"Net ↑ {_human_bytes(net_sent_per_s)}/s  "
                f"↓ {_human_bytes(net_recv_per_s)}/s"
            ),
        }
        data["available"] = True
    except (ImportError, Exception):
        data.setdefault("available", False)
        data.setdefault(
            "process",
            {
                "pid": os.getpid(),
                "rss": "—",
                "rss_bytes": 0.0,
                "threads": "—",
                "uptime": _uptime_text(time.monotonic() - _PROCESS_START),
                "cpu_percent": 0.0,
            },
        )
    return data


class _MetricRow(Widget):
    def __init__(self, key: str, value: str) -> None:
        super().__init__(classes="monitor-row")
        self._key = key
        self._value = value

    def compose(self) -> ComposeResult:
        yield Label(self._key, classes="monitor-row-key")
        yield Label(self._value, classes="monitor-row-value")


class _MetricBlock(Static):
    def __init__(
        self, title: str, bar_pct: float | None, rows: list[tuple[str, str]]
    ) -> None:
        super().__init__(classes="monitor-block")
        self._title = title
        self._bar_pct = bar_pct
        self._rows = rows

    def compose(self) -> ComposeResult:
        yield Label(self._title, classes="monitor-block-title")
        if self._bar_pct is not None:
            pct = max(0.0, min(100.0, self._bar_pct))
            bar_class = "monitor-bar-ok"
            if pct > 85:
                bar_class = "monitor-bar-critical"
            elif pct > 70:
                bar_class = "monitor-bar-warning"
            yield Label(
                f"  {_bar(pct)}  {pct:.0f}%",
                classes=f"monitor-bar {bar_class}",
            )
        for key, value in self._rows:
            yield _MetricRow(str(key), str(value))


class MonitorTab(Widget):
    can_focus = True

    def __init__(self) -> None:
        super().__init__(id="monitor-tab")
        self._data: dict[str, Any] = {}
        self._timer: Timer | None = None
        self._last_refreshed: str = ""
        self._loading = False

    @staticmethod
    def _iter_left_column_blocks(cpu: dict, disk: dict, network: dict) -> ComposeResult:
        yield _MetricBlock(
            "CPU",
            cpu.get("percent", 0),
            [
                ("Cores", str(cpu.get("cores", "—"))),
                ("Load avg", str(cpu.get("load_avg", "—"))),
            ],
        )
        yield _MetricBlock(
            "DISK (/)",
            disk.get("percent", 0),
            [
                ("Used", f"{disk.get('used', '—')} / {disk.get('total', '—')}"),
                ("Free", str(disk.get("free", "—"))),
            ],
        )
        yield _MetricBlock(
            "NETWORK",
            None,
            [
                ("Net", str(network.get("summary", "—"))),
                (
                    "Sent/s",
                    _human_bytes(float(network.get("net_sent_per_s", 0.0))) + "/s",
                ),
                (
                    "Recv/s",
                    _human_bytes(float(network.get("net_recv_per_s", 0.0))) + "/s",
                ),
            ],
        )

    @staticmethod
    def _iter_right_column_blocks(mem: dict, proc: dict) -> ComposeResult:
        yield _MetricBlock(
            "MEMORY",
            mem.get("percent", 0),
            [
                ("Used", f"{mem.get('used', '—')} / {mem.get('total', '—')}"),
                (
                    "Swap",
                    f"{mem.get('swap_used', '—')} / {mem.get('swap_total', '—')}",
                ),
            ],
        )
        yield _MetricBlock(
            "PROCESS",
            proc.get("cpu_percent", 0),
            [
                ("PID", str(proc.get("pid", "—"))),
                ("RSS", str(proc.get("rss", "—"))),
                ("Threads", str(proc.get("threads", "—"))),
                ("Uptime", str(proc.get("uptime", "—"))),
            ],
        )

    @staticmethod
    def _iter_unavailable_state(data: dict) -> ComposeResult:
        yield Static(
            "psutil not installed — install with: pip install psutil\n"
            "Showing basic process info only.",
            classes="dim-hint",
        )
        proc = data.get("process", {})
        yield _MetricBlock(
            "PROCESS",
            None,
            [
                ("PID", str(proc.get("pid", "—"))),
                ("Uptime", str(proc.get("uptime", "—"))),
            ],
        )

    def compose(self) -> ComposeResult:
        data = self._data
        if self._loading and not data:
            yield LoadingIndicator(id="monitor-loading")
            return
        if not data:
            yield Static("Collecting metrics…", classes="dim-hint")
            return
        if not data.get("available", False):
            yield from self._iter_unavailable_state(data)
            return

        cpu = data.get("cpu", {})
        mem = data.get("memory", {})
        disk = data.get("disk", {})
        proc = data.get("process", {})
        network = data.get("network", {})

        with ScrollableContainer(id="monitor-body"):
            if self._last_refreshed:
                yield Label(
                    f"Last refreshed {self._last_refreshed}",
                    classes="monitor-timestamp",
                )
            if self._loading:
                yield LoadingIndicator(classes="tab-loading-indicator")
            with Horizontal(classes="monitor-grid"):
                with Vertical(classes="monitor-col"):
                    yield from self._iter_left_column_blocks(cpu, disk, network)
                with Vertical(classes="monitor-col"):
                    yield from self._iter_right_column_blocks(mem, proc)

    def on_show(self) -> None:
        self._schedule_refresh()
        if self._timer is None:
            self._timer = self.set_interval(2, self._schedule_refresh)

    def on_hide(self) -> None:
        self._stop_timer()

    def on_unmount(self) -> None:
        self._stop_timer()

    def action_refresh(self) -> None:
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        self.run_worker(self._async_refresh(), exclusive=True)

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    async def _async_refresh(self) -> None:
        import asyncio

        self._loading = True
        await self.recompose()
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, _collect_metrics)
        self._data = data
        self._loading = False
        self._last_refreshed = datetime.now().strftime("%H:%M:%S")
        await self.recompose()
