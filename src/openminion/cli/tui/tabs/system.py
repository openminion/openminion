from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer
from textual.css.query import QueryError
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Label, LoadingIndicator, Static


class _InfoRow(Widget):
    def __init__(self, key: str, value: str, *, classes: str = "") -> None:
        super().__init__(classes=f"info-row {classes}".strip())
        self._key = key
        self._value = value

    def compose(self) -> ComposeResult:
        yield Label(self._key, classes="info-row-key")
        yield Label(self._value, classes="info-row-value")


class _InfoBlock(Widget):
    def __init__(self, title: str, rows: list[tuple[str, str]], **kwargs) -> None:
        super().__init__(classes="info-block", **kwargs)
        self._title = title
        self._rows = rows

    def compose(self) -> ComposeResult:
        yield Label(self._title, classes="info-block-title")
        for key, val in self._rows:
            yield _InfoRow(str(key), str(val))


class _PluginBlock(Widget):
    def __init__(self, plugins: list[dict]) -> None:
        super().__init__(classes="info-block")
        self._plugins = plugins

    def compose(self) -> ComposeResult:
        active = sum(1 for plugin in self._plugins if plugin.get("enabled"))
        yield Label(
            f"PLUGINS ({active} active)",
            classes="info-block-title",
        )
        if not self._plugins:
            yield _InfoRow("(none)", "")
            return
        for plugin in self._plugins:
            prefix = "✓ enabled" if plugin.get("enabled") else "✗ disabled"
            yield _InfoRow(prefix, str(plugin.get("name", "?")))


class _SidecarBlock(Widget):
    def __init__(self, sidecar: dict) -> None:
        super().__init__(classes="info-block")
        self._sidecar = sidecar

    def compose(self) -> ComposeResult:
        name = str(self._sidecar.get("name", "pinchtab") or "pinchtab")
        running = bool(self._sidecar.get("running"))
        consent = str(self._sidecar.get("consent", "unknown") or "unknown")
        yield Label("SIDECAR", classes="info-block-title")
        yield _InfoRow("Name", name)
        yield _InfoRow("Status", "running" if running else "stopped")
        yield _InfoRow("PID", str(self._sidecar.get("pid", "—")))
        yield _InfoRow("Consent", consent)
        with Horizontal(classes="system-sidecar-actions"):
            yield Button(
                "Stop" if running else "Start",
                id="system-sidecar-toggle",
                variant="primary",
            )
            yield Button(
                "Revoke consent" if consent == "approved" else "Approve consent",
                id="system-sidecar-consent",
            )


class SystemTab(Widget):
    can_focus = True

    BINDINGS = [("r", "refresh", "Refresh")]

    def __init__(self, provider=None) -> None:
        super().__init__(id="system-tab")
        self._provider = provider
        self._data: dict = {}
        self._loading = False
        self._refresh_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        if self._provider is None:
            yield Static(
                "No data — runtime provider not available.\n"
                "Start with a config to see daemon, agent, and storage info.",
                classes="tab-empty-notice",
            )
            return

        if self._loading and not self._data:
            yield LoadingIndicator(id="system-loading")
            return

        d = self._data
        daemon = d.get("daemon", {})
        agent = d.get("agent", {})
        storage = d.get("storage", {})
        telem = d.get("telemetry", {})
        plugins = d.get("plugins", [])
        sidecar = d.get("sidecar", {})

        with Horizontal(id="system-body"):
            with ScrollableContainer(id="system-left"):
                if self._loading:
                    yield LoadingIndicator(classes="tab-loading-indicator")
                yield _InfoBlock(
                    "DAEMON",
                    [
                        ("Mode", str(daemon.get("mode", "—"))),
                        ("Endpoint", str(daemon.get("endpoint", "—"))),
                        ("PID", str(daemon.get("pid", "—"))),
                        ("Uptime", str(daemon.get("uptime", "—"))),
                    ],
                )
                yield _InfoBlock(
                    "AGENT",
                    [
                        ("Model", str(agent.get("model", "—"))),
                        ("Mode", str(agent.get("runtime_mode", "—"))),
                        ("Brain", str(agent.get("brain_mode", "—"))),
                        ("Provider", str(agent.get("provider", "—"))),
                    ],
                )
                yield _PluginBlock(list(plugins))
            with ScrollableContainer(id="system-right"):
                if self._loading:
                    yield LoadingIndicator(classes="tab-loading-indicator")
                yield _InfoBlock(
                    "STORAGE",
                    [
                        ("DB size", str(storage.get("db_size", "—"))),
                        ("Sessions", str(storage.get("session_count", "—"))),
                        ("Events", str(storage.get("event_count", "—"))),
                        ("Memory rows", str(storage.get("memory_count", "—"))),
                    ],
                )
                yield _InfoBlock(
                    "TELEMETRY (1h)",
                    [
                        ("Turns", str(telem.get("turns", "—"))),
                        ("Tool calls", str(telem.get("tool_calls", "—"))),
                        ("Errors", str(telem.get("errors", "—"))),
                        ("Avg latency", str(telem.get("avg_latency", "—"))),
                    ],
                )
                yield _SidecarBlock(sidecar)

    async def on_mount(self) -> None:
        self._refresh_timer = self.set_interval(5, self._refresh_tick)
        await self.refresh_data()
        self.call_after_refresh(self._sync_layout_mode)

    def on_resize(self, event) -> None:
        del event
        self.call_after_refresh(self._sync_layout_mode)

    def on_unmount(self) -> None:
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None

    async def refresh_data(self) -> None:
        if self._provider is None:
            return
        self._loading = True
        await self.recompose()
        self._data = {
            "daemon": self._provider.get_daemon_status(),
            "agent": self._provider.get_agent_info(),
            "storage": self._provider.get_storage_stats(),
            "telemetry": self._provider.get_telemetry_summary(),
            "plugins": self._provider.get_plugin_status(),
            "sidecar": self._get_sidecar_status(),
        }
        self._loading = False
        await self.recompose()
        self._sync_layout_mode()

    def _get_sidecar_status(self) -> dict:
        getter = getattr(self._provider, "get_sidecar_status", None)
        if callable(getter):
            try:
                status = getter()
                if isinstance(status, dict):
                    return status
            except (AttributeError, TypeError, ValueError):
                pass
        return {
            "name": "pinchtab",
            "running": False,
            "pid": "—",
            "consent": "unknown",
        }

    def _refresh_tick(self) -> None:
        self.run_worker(self.refresh_data(), exclusive=True)

    def action_refresh(self) -> None:
        self.run_worker(self.refresh_data(), exclusive=True)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "system-sidecar-toggle":
            await self._run_provider_action(
                "stop_sidecar"
                if bool(self._data.get("sidecar", {}).get("running"))
                else "start_sidecar"
            )
            event.stop()
            return
        if button_id == "system-sidecar-consent":
            approved = (
                str(self._data.get("sidecar", {}).get("consent", "unknown")).lower()
                != "approved"
            )
            await self._run_provider_action("set_sidecar_consent", approved)
            event.stop()

    def _sync_layout_mode(self) -> None:
        try:
            body = self.query_one("#system-body", Horizontal)
        except QueryError:
            return
        if self.app.size.width < 100:
            body.add_class("--stacked")
        else:
            body.remove_class("--stacked")

    async def _run_provider_action(self, method_name: str, *args: object) -> None:
        method = getattr(self._provider, method_name, None)
        if callable(method):
            try:
                method(*args)
            except Exception:
                pass
        await self.refresh_data()
