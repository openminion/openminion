from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import QueryError
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    OptionList,
    Static,
    TabbedContent,
    TabPane,
)

from openminion.cli.parser.contracts import (
    AgentRuntimeAPI,
    ChatRuntimeAPI,
    ProviderBundle,
)
from openminion.cli.presentation import resolve_runtime_data_root, slash_help_rows
from .screen_actions import MainScreenActionsMixin
from .tabs import (
    AgentsTab,
    ChatTab,
    CronTab,
    MemoryTab,
    MonitorTab,
    PolicyTab,
    SessionsTab,
    SystemTab,
    TasksTab,
    ThirdBrainTab,
)
from .tabs.tasks import PolicyUpdateNeeded


class AppHeader(Static):
    def __init__(self, agent_id: str, session_id: str, transport: str) -> None:
        super().__init__(id="app-header")
        self._agent_id = agent_id
        self._session_id = session_id
        self._transport = transport

    def _short_sess(self, sid: str) -> str:
        from openminion.cli.presentation.header import shorten_session_id

        return shorten_session_id(sid, length=12)

    @staticmethod
    def _clock_text() -> str:
        from datetime import datetime

        now = datetime.now()
        return now.strftime("%Y-%m-%d %H:%M")

    def compose(self) -> ComposeResult:
        yield Label("◆  OpenMinion", id="header-title")
        yield Label(
            f"{self._agent_id}  ·  {self._short_sess(self._session_id)}",
            id="header-badge",
        )
        yield Label(self._clock_text(), id="header-clock")
        yield Label(self._transport, id="header-transport")
        if "demo" in self._transport.lower():
            yield Label("[DEMO]", id="header-demo-badge")
        yield Label("●", id="header-status-dot")

    def on_mount(self) -> None:
        self.set_interval(30, self._update_clock)

    def _update_clock(self) -> None:
        try:
            self.query_one("#header-clock", Label).update(self._clock_text())
        except (QueryError, AttributeError):
            pass

    def update_badge(self, session_id: str, agent_id: str) -> None:
        self.query_one("#header-badge", Label).update(
            f"{agent_id}  ·  {self._short_sess(session_id)}"
        )
        self._agent_id = agent_id
        self._session_id = session_id

    def update_transport(self, transport: str) -> None:
        self.query_one("#header-transport", Label).update(transport)

    def update_status(self, state: str) -> None:
        dot = self.query_one("#header-status-dot", Label)
        for cls in ("--ok", "--warning", "--error", "--offline"):
            dot.remove_class(cls)
        dot.add_class(f"--{state}")


class FooterBar(Widget):
    class TabSwitch(Message):
        def __init__(self, tab_id: str) -> None:
            super().__init__()
            self.tab_id = tab_id

    _ALL_TABS = [
        ("^1", "Chat", "tab-chat"),
        ("^2", "Tasks", "tab-tasks"),
        ("^3", "Cron", "tab-cron"),
        ("^4", "Sess", "tab-sessions"),
        ("^5", "Sys", "tab-system"),
        ("^6", "Policy", "tab-policy"),
        ("^7", "Mem", "tab-memory"),
        ("^8", "Mon", "tab-monitor"),
        ("^9", "Agents", "tab-agents"),
        ("^0", "Graph", "tab-third-brain"),
    ]
    _CONTEXT_HINTS = {
        "tab-chat": "^B sidebar  ^N new  ^A agent  ^F search  ^Y copy  ^L multiline",
        "tab-tasks": "r refresh  a/d approve/deny  filter: Active/Done/All",
        "tab-cron": "r refresh  e toggle enable/disable",
        "tab-sessions": "r resume  / search  n rename  x close  auto-refreshes",
        "tab-system": "r refresh  auto-refreshes every 5s",
        "tab-policy": "r refresh  click Revoke on grants",
        "tab-memory": "/ search  click record for detail",
        "tab-monitor": "r refresh  auto-refreshes every 2s while visible",
        "tab-agents": "r refresh  n new  e edit  d delete  p preview  click to view",
        "tab-third-brain": "/ search  r refresh  search/neighbors/path  copy/export/open source",
    }

    def __init__(self, visible_tab_ids: list[str] | None = None) -> None:
        super().__init__()
        visible = (
            set(visible_tab_ids)
            if visible_tab_ids is not None
            else {t[2] for t in self._ALL_TABS}
        )
        self._tabs = [t for t in self._ALL_TABS if t[2] in visible]

    def compose(self) -> ComposeResult:
        with Horizontal(id="footer-nav"):
            for key, label, tab_id in self._tabs:
                yield Button(
                    f"{key} {label}",
                    id=f"footer-{tab_id}",
                    classes="footer-tab-btn",
                )
            yield Label("F1 help", id="footer-help-hint", classes="footer-hint-text")
        yield Label("", id="footer-context-hints", classes="footer-hint-text")

    def on_mount(self) -> None:
        first = self._tabs[0][2] if self._tabs else ""
        self.set_context_hints(first)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id.startswith("footer-tab-"):
            self.post_message(FooterBar.TabSwitch(btn_id.removeprefix("footer-")))
            event.stop()

    def set_context_hints(self, tab_id: str) -> None:
        context = self._CONTEXT_HINTS.get(tab_id, "")
        self.query_one("#footer-context-hints", Label).update(context)

    def update_badge(self, tab_id: str, count: int) -> None:
        btn_id = f"footer-{tab_id}"
        try:
            btn = self.query_one(f"#{btn_id}", Button)
        except (QueryError, AttributeError):
            return
        for key, base_label, tid in self._ALL_TABS:
            if tid == tab_id:
                if count > 0:
                    btn.label = f"{key} {base_label}({count})"
                else:
                    btn.label = f"{key} {base_label}"
                break


TAB_CHAT: str = "tab-chat"

_PALETTE_ENTRIES: list[tuple[str, str, str]] = [
    ("Chat", "Switch to Chat tab", "tab-chat"),
    ("Tasks", "Switch to Tasks tab", "tab-tasks"),
    ("Cron", "Switch to Cron tab", "tab-cron"),
    ("Sessions", "Switch to Sessions tab", "tab-sessions"),
    ("System", "Switch to System tab", "tab-system"),
    ("Policy", "Switch to Policy tab", "tab-policy"),
    ("Memory", "Switch to Memory tab", "tab-memory"),
    ("Monitor", "System resource monitor", "tab-monitor"),
    ("Agents", "Agent profile management", "tab-agents"),
    ("Graph", "Third-brain workbench", "tab-third-brain"),
    ("/new", "Start a new session", "cmd-new"),
    ("/clear", "Clear chat history", "cmd-clear"),
    ("/tools", "List available tools", "cmd-tools"),
    ("/status", "Show agent/session/transport", "cmd-status"),
    ("/debug", "Extended debug info", "cmd-debug"),
    ("/sidebar", "Toggle sidebar", "cmd-sidebar"),
    ("/help", "Keyboard reference", "cmd-help"),
    ("/exit", "Quit the TUI", "cmd-exit"),
    ("/trust", "Grant session trust", "cmd-trust"),
    ("/untrust", "Revoke session trust", "cmd-untrust"),
    ("/grants", "List active grants", "cmd-grants"),
    ("/artifacts", "Inspect last turn artifacts", "cmd-artifacts"),
    ("/identity", "Identity profile management", "cmd-identity"),
    ("/skill", "Skill ingestion and listing", "cmd-skill"),
    ("/sidecar", "Sidecar daemon management", "cmd-sidecar"),
    ("/pair", "Daemon pairing management", "cmd-pair"),
]


class CommandPaletteScreen(Screen[str | None]):
    BINDINGS = [
        ("escape", "dismiss_palette", "Close"),
        ("enter", "select_entry", "Select"),
        ("up", "move_up", "Up"),
        ("down", "move_down", "Down"),
    ]

    def __init__(self, entries: list[tuple[str, str, str]] | None = None) -> None:
        super().__init__()
        self._entries = entries or list(_PALETTE_ENTRIES)
        self._filtered: list[tuple[str, str, str]] = list(self._entries)

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-overlay"), Vertical(id="palette-dialog"):
            yield Input(placeholder="Type to search…", id="palette-input")
            yield OptionList(
                *[f"{label}  —  {desc}" for label, desc, _ in self._filtered],
                id="palette-list",
            )

    def on_mount(self) -> None:
        self.query_one("#palette-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "palette-input":
            return
        query = event.value.strip().lower()
        self._filtered = (
            [e for e in self._entries if query in e[0].lower() or query in e[1].lower()]
            if query
            else list(self._entries)
        )
        option_list = self.query_one("#palette-list", OptionList)
        option_list.clear_options()
        for label, desc, _ in self._filtered:
            option_list.add_option(f"{label}  —  {desc}")
        if self._filtered:
            option_list.highlighted = 0

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_select_entry()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "palette-input":
            self.action_select_entry()

    def action_select_entry(self) -> None:
        option_list = self.query_one("#palette-list", OptionList)
        idx = option_list.highlighted
        if idx is not None and 0 <= idx < len(self._filtered):
            self.dismiss(self._filtered[idx][2])
        else:
            self.dismiss(None)

    def action_dismiss_palette(self) -> None:
        self.dismiss(None)

    def action_move_up(self) -> None:
        ol = self.query_one("#palette-list", OptionList)
        if ol.highlighted is not None and ol.highlighted > 0:
            ol.highlighted -= 1

    def action_move_down(self) -> None:
        ol = self.query_one("#palette-list", OptionList)
        if ol.highlighted is not None and ol.highlighted < ol.option_count - 1:
            ol.highlighted += 1


class RuntimeBanner(Widget):
    def __init__(self, message: str) -> None:
        super().__init__(id="runtime-banner")
        self._message = message

    def compose(self) -> ComposeResult:
        yield Label(self._message, id="runtime-banner-text")

    def on_mount(self) -> None:
        self.set_timer(10, self.dismiss)

    def dismiss(self) -> None:
        self.add_class("--hidden")

    @property
    def is_visible(self) -> bool:
        return not self.has_class("--hidden")


_DEBUG_ICON = {
    "llm": "🤖",
    "tool": "⚙",
    "ctx": "📋",
}


class DebugPane(Widget):
    def __init__(self, *, runtime: AgentRuntimeAPI, providers: ProviderBundle) -> None:
        super().__init__(id="debug-pane")
        self._runtime = runtime
        self._providers = providers
        self._events: list[dict[str, str]] = []
        self._event_filter = "all"
        self._timer = None
        self.add_class("--hidden")

    def compose(self) -> ComposeResult:
        with Vertical(id="debug-pane-inner"):
            with Horizontal(id="debug-filter-bar"):
                for filter_name, label in (
                    ("all", "All"),
                    ("llm", "LLM"),
                    ("tool", "Tool"),
                    ("ctx", "Ctx"),
                ):
                    classes = "debug-filter-btn"
                    if filter_name == self._event_filter:
                        classes += " --selected"
                    yield Button(
                        label,
                        id=f"debug-filter-{filter_name}",
                        classes=classes,
                    )
            if not self._events:
                yield Label("No debug events yet.", classes="dim-hint")
                return
            table = DataTable(id="debug-events")
            table.add_columns("Time", "Type", "Summary")
            for event in self._filtered_events():
                table.add_row(
                    event.get("ts", ""),
                    f"{_DEBUG_ICON.get(event.get('bucket', ''), '·')} {event.get('bucket', '')}",
                    event.get("summary", ""),
                )
            yield table

    def on_show(self) -> None:
        if self._timer is None:
            self._timer = self.set_interval(0.5, self._refresh_tick)

    def on_hide(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def toggle(self) -> None:
        if self.has_class("--hidden"):
            self.remove_class("--hidden")
            self.refresh_events()
        else:
            self.add_class("--hidden")

    def refresh_events(self) -> None:
        provider = self._providers.sessions
        if provider is None:
            return
        getter = getattr(provider, "get_session_timeline", None)
        if not callable(getter):
            return
        try:
            timeline = getter(self._runtime.session_id)
        except (QueryError, AttributeError):
            return
        events: list[dict[str, str]] = []
        for event in timeline[-50:]:
            event_type = str(event.get("event_type", "") or "")
            events.append(
                {
                    "ts": str(event.get("ts", "") or ""),
                    "bucket": self._bucket_for(event_type),
                    "summary": f"{event_type}  {str(event.get('detail', '') or '').strip()}".strip(),
                }
            )
        self._events = events
        self.app.call_later(self.recompose)

    def _refresh_tick(self) -> None:
        self.refresh_events()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("debug-filter-"):
            return
        self._event_filter = button_id.removeprefix("debug-filter-")
        self.app.call_later(self.recompose)
        event.stop()

    def _filtered_events(self) -> list[dict[str, str]]:
        if self._event_filter == "all":
            return list(self._events)
        return [
            event for event in self._events if event.get("bucket") == self._event_filter
        ]

    @staticmethod
    def _bucket_for(event_type: str) -> str:
        normalized = str(event_type or "").lower()
        if normalized.startswith("llm."):
            return "llm"
        if normalized.startswith("tool."):
            return "tool"
        return "ctx"


class OnboardingWizardScreen(Screen):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        *,
        config_path: Path,
        home_root: Path,
        data_root: Path,
        agent_id: str,
    ) -> None:
        super().__init__()
        self._config_path = Path(config_path)
        self._home_root = Path(home_root)
        self._data_root = Path(data_root)
        self._agent_id = str(agent_id or "openminion").strip() or "openminion"
        self._step = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="onboarding-overlay"):
            with Vertical(id="onboarding-dialog"):
                yield Label("OpenMinion First-Run Setup", classes="modal-title")
                yield Label(
                    "Step 1 of 3 — config path",
                    id="onboarding-step-label",
                )
                with Vertical(id="onboarding-step-config", classes="wizard-step"):
                    yield Label("Config path:")
                    yield Input(
                        value=str(self._config_path),
                        id="onboarding-config-path",
                    )
                with Vertical(
                    id="onboarding-step-provider",
                    classes="wizard-step --hidden",
                ):
                    yield Label("Provider:")
                    yield Input(
                        value="openrouter",
                        id="onboarding-provider",
                        placeholder="openrouter / openai / anthropic / ollama / echo",
                    )
                    yield Label("Model:")
                    yield Input(
                        value="anthropic/claude-3-haiku",
                        id="onboarding-model",
                        placeholder="Model name",
                    )
                with Vertical(
                    id="onboarding-step-agent",
                    classes="wizard-step --hidden",
                ):
                    yield Label("Initial agent id:")
                    yield Input(
                        value=self._agent_id,
                        id="onboarding-agent-id",
                        placeholder="hello-agent",
                    )
                with Horizontal(id="onboarding-buttons"):
                    yield Button("Back", id="onboarding-back")
                    yield Button("Next", id="onboarding-next", variant="primary")
                    yield Button("Cancel", id="onboarding-cancel")

    def on_mount(self) -> None:
        self._sync_step()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "onboarding-back":
            self._step = max(0, self._step - 1)
            self._sync_step()
            return
        if button_id == "onboarding-next":
            if self._step < 2:
                self._step += 1
                self._sync_step()
                return
            self._finish()
            return
        self.app.exit(result="")

    def action_cancel(self) -> None:
        self.app.exit(result="")

    def _sync_step(self) -> None:
        labels = {
            0: "Step 1 of 3 — config path",
            1: "Step 2 of 3 — provider and model",
            2: "Step 3 of 3 — initial agent",
        }
        step_ids = (
            "onboarding-step-config",
            "onboarding-step-provider",
            "onboarding-step-agent",
        )
        try:
            self.query_one("#onboarding-step-label", Label).update(labels[self._step])
        except (QueryError, AttributeError):
            pass
        for index, step_id in enumerate(step_ids):
            try:
                step = self.query_one(f"#{step_id}", Vertical)
            except (QueryError, AttributeError):
                continue
            if index == self._step:
                step.remove_class("--hidden")
            else:
                step.add_class("--hidden")
        try:
            back = self.query_one("#onboarding-back", Button)
            back.disabled = self._step == 0
            next_button = self.query_one("#onboarding-next", Button)
            next_button.label = "Create config" if self._step == 2 else "Next"
        except (QueryError, AttributeError):
            pass
        focus_ids = {
            0: "#onboarding-config-path",
            1: "#onboarding-provider",
            2: "#onboarding-agent-id",
        }
        try:
            self.query_one(focus_ids[self._step], Input).focus()
        except (QueryError, AttributeError):
            pass

    def _finish(self) -> None:
        from openminion.base.config import (
            AgentProfileConfig,
            OpenMinionConfig,
            save_config,
        )

        config_path = Path(
            self.query_one("#onboarding-config-path", Input).value.strip()
        ).expanduser()
        provider = str(
            self.query_one("#onboarding-provider", Input).value.strip() or "openrouter"
        ).lower()
        model = str(
            self.query_one("#onboarding-model", Input).value.strip()
            or "anthropic/claude-3-haiku"
        )
        agent_id = str(
            self.query_one("#onboarding-agent-id", Input).value.strip()
            or self._agent_id
        )

        config = OpenMinionConfig()
        config.runtime.demo_mode = provider == "echo"
        config.storage.path = str(
            (self._data_root / "state" / "openminion.db").resolve(strict=False)
        )
        if provider == "openai":
            config.providers.openai.model = model
        elif provider == "anthropic":
            config.providers.anthropic.model = model
        elif provider == "openrouter":
            config.providers.openrouter.model = model
        elif provider == "ollama":
            config.providers.ollama.model = model
        config.agents = {
            agent_id: AgentProfileConfig(
                name=agent_id,
                provider=provider,
            )
        }
        saved_path = save_config(
            config,
            str(config_path),
            home_root=self._home_root,
        )
        self.app.exit(result=str(saved_path))


class MainScreen(MainScreenActionsMixin, Screen):
    BINDINGS = [
        ("ctrl+1,alt+1", "switch_tab('tab-chat')", "Chat"),
        ("ctrl+2,alt+2", "switch_tab('tab-tasks')", "Tasks"),
        ("ctrl+3,alt+3", "switch_tab('tab-cron')", "Cron"),
        ("ctrl+4,alt+4", "switch_tab('tab-sessions')", "Sessions"),
        ("ctrl+5,alt+5", "switch_tab('tab-system')", "System"),
        ("ctrl+6,alt+6", "switch_tab('tab-policy')", "Policy"),
        ("ctrl+7,alt+7", "switch_tab('tab-memory')", "Memory"),
        ("ctrl+8,alt+8", "switch_tab('tab-monitor')", "Monitor"),
        ("ctrl+9,alt+9", "switch_tab('tab-agents')", "Agents"),
        ("ctrl+0,alt+0", "switch_tab('tab-third-brain')", "Graph"),
        ("/", "focus_tab_search", "Search"),
        ("ctrl+p", "command_palette", "Commands"),
        ("ctrl+d", "toggle_debug", "Debug"),
        ("f1", "show_help", "Help"),
    ]

    def __init__(
        self,
        runtime: AgentRuntimeAPI,
        providers: ProviderBundle | None = None,
        initial_tab: str | None = None,
    ) -> None:
        super().__init__()
        self._runtime = runtime
        self._providers = providers or ProviderBundle()
        self._initial_tab = initial_tab
        self._degraded_mode = (
            "demo" not in str(runtime.transport).lower()
            and "daemon" not in str(runtime.transport).lower()
        )
        if not isinstance(runtime, ChatRuntimeAPI):
            self.BINDINGS = [b for b in self.BINDINGS if TAB_CHAT not in str(b)]

    @property
    def _has_chat(self) -> bool:
        return isinstance(self._runtime, ChatRuntimeAPI)

    def compose(self) -> ComposeResult:
        rt = self._runtime
        p = self._providers
        yield AppHeader(
            agent_id=rt.agent_id,
            session_id=rt.session_id,
            transport=rt.transport,
        )
        if self._degraded_mode:
            yield RuntimeBanner("Running without daemon — some features unavailable")
        if self._initial_tab:
            initial = self._initial_tab
        elif self._has_chat:
            initial = "tab-chat"
        else:
            initial = "tab-tasks"
        visible_tabs: list[str] = []
        if self._has_chat:
            visible_tabs.append("tab-chat")
        visible_tabs += [
            "tab-tasks",
            "tab-cron",
            "tab-sessions",
            "tab-system",
            "tab-policy",
            "tab-memory",
            "tab-monitor",
            "tab-agents",
            "tab-third-brain",
        ]
        with Vertical(id="main-screen-stack"):
            with TabbedContent(initial=initial, id="main-tabs"):
                if self._has_chat:
                    with TabPane("Chat", id="tab-chat"):
                        yield ChatTab(rt, sessions_provider=p.sessions)  # type: ignore[arg-type]
                with TabPane("Tasks", id="tab-tasks"):
                    yield TasksTab(p.tasks)
                with TabPane("Cron", id="tab-cron"):
                    yield CronTab(p.cron)
                with TabPane("Sessions", id="tab-sessions"):
                    yield SessionsTab(p.sessions)
                with TabPane("System", id="tab-system"):
                    yield SystemTab(p.system)
                with TabPane("Policy", id="tab-policy"):
                    yield PolicyTab(p.policy)
                with TabPane("Memory", id="tab-memory"):
                    yield MemoryTab(p.memory)
                with TabPane("Monitor", id="tab-monitor"):
                    yield MonitorTab()
                with TabPane("Agents", id="tab-agents"):
                    yield AgentsTab(p.agents)
                with TabPane("Graph", id="tab-third-brain"):
                    yield ThirdBrainTab(
                        p.provider,
                        working_dir=str(getattr(rt, "working_dir", "") or ""),
                        data_root=resolve_runtime_data_root(rt),
                    )
            yield DebugPane(runtime=rt, providers=p)
        yield FooterBar(visible_tabs)

    def on_chat_tab_badge_update(self, event: ChatTab.BadgeUpdate) -> None:
        self.query_one(AppHeader).update_badge(event.session_id, event.agent_id)

    def on_agents_tab_switch_requested(self, event: AgentsTab.SwitchRequested) -> None:
        if not self._has_chat:
            return
        chat_tab = self.query_one(ChatTab)
        chat_tab._do_switch_agent(event.agent_id)
        self.action_switch_tab("tab-chat")

    async def on_policy_update_needed(self, event: PolicyUpdateNeeded) -> None:
        policy_tab = self.query_one(PolicyTab)
        await policy_tab.refresh_from_provider()
        self._refresh_tab_badges()

    def on_mount(self) -> None:
        self.call_after_refresh(self._refresh_tab_badges)
        p = self._providers
        has_providers = any(
            [
                p.system,
                p.sessions,
                p.tasks,
                p.cron,
                p.policy,
                p.agents,
                p.provider,
            ]
        )
        header = self.query_one(AppHeader)
        if self._degraded_mode:
            header.update_status("warning")
        elif has_providers:
            header.update_status("ok")
        else:
            header.update_status("offline")

    def _refresh_tab_badges(self) -> None:
        footer = self.query_one(FooterBar)
        p = self._providers
        try:
            if p.tasks is not None:
                tasks = p.tasks.list_tasks()
                active = sum(
                    1
                    for t in tasks
                    if str(t.get("status", "")).lower()
                    in ("active", "pending", "waiting")
                )
                footer.update_badge("tab-tasks", active)
        except (QueryError, AttributeError):
            pass
        try:
            if p.policy is not None:
                pending = p.policy.list_pending_decisions()
                footer.update_badge("tab-policy", len(pending))
        except (QueryError, AttributeError):
            pass
        try:
            if p.cron is not None:
                jobs = list(self.query_one(CronTab)._jobs)
                enabled = sum(1 for job in jobs if bool(job.get("enabled")))
                footer.update_badge("tab-cron", enabled)
        except (QueryError, AttributeError):
            pass
        try:
            if p.sessions is not None:
                sessions = list(self.query_one(SessionsTab)._all_sessions)
                footer.update_badge("tab-sessions", len(sessions))
        except (QueryError, AttributeError):
            pass
        try:
            if p.memory is not None:
                records = list(self.query_one(MemoryTab)._records)
                footer.update_badge("tab-memory", len(records))
        except (QueryError, AttributeError):
            pass
        try:
            if p.provider is not None:
                results = list(self.query_one(ThirdBrainTab)._results)
                footer.update_badge("tab-third-brain", len(results))
        except (QueryError, AttributeError):
            pass

    _TAB_FOCUS_TARGETS: dict[str, type[Widget]] = {}


class HelpScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Close"),
        ("q", "app.pop_screen", "Close"),
        ("f1", "app.pop_screen", "Close"),
    ]

    @staticmethod
    def _chat_command_entries() -> list[tuple[str, str]]:
        return list(slash_help_rows())

    SECTIONS = [
        (
            "Navigation",
            [
                (
                    "Ctrl+1 … Ctrl+0",
                    "Chat / Tasks / Cron / Sess / Sys / Policy / Mem / Mon / Agents / Graph",
                ),
                ("Ctrl+P", "Command palette (fuzzy search)"),
                ("F1", "This help screen"),
            ],
        ),
        (
            "Chat",
            [
                ("Ctrl+N", "New session"),
                ("Ctrl+A", "Switch agent"),
                ("Ctrl+B", "Toggle sidebar"),
                ("Ctrl+F", "Search messages"),
                ("Ctrl+Y", "Copy last message to clipboard"),
                ("Ctrl+L", "Toggle multiline input"),
                ("↑ / ↓ in input", "Message history"),
                ("Esc", "Focus input / close search"),
            ],
        ),
        (
            "Chat commands",
            [],
        ),
        (
            "Tasks tab  (Ctrl+2)",
            [
                ("a / d", "Approve / deny pending decision"),
                ("Tab", "Cycle filter: Active / Done / All"),
            ],
        ),
        (
            "Cron tab  (Ctrl+3)",
            [
                ("e", "Toggle enable/disable selected job"),
            ],
        ),
        (
            "Sessions tab  (Ctrl+4)",
            [
                ("/  (search)", "Filter sessions"),
                ("r", "Resume selected session in Chat"),
                ("n", "Rename selected session"),
                ("x", "Close/archive session"),
                ("d", "Delete selected session"),
            ],
        ),
        (
            "System tab  (Ctrl+5)",
            [
                ("r", "Refresh system info"),
            ],
        ),
        (
            "Policy tab  (Ctrl+6)",
            [
                ("Tab", "Cycle view: Decisions / Grants"),
            ],
        ),
        (
            "Memory tab  (Ctrl+7)",
            [
                ("/  (search)", "Search memory records"),
            ],
        ),
        (
            "Monitor tab  (Ctrl+8)",
            [
                ("r", "Refresh metrics now"),
                ("—", "Auto-refreshes every 2s while visible"),
            ],
        ),
        (
            "Agents tab  (Ctrl+9)",
            [
                ("r", "Refresh agent list"),
                ("n", "Create new agent profile"),
                ("e", "Edit selected agent profile"),
                ("d", "Delete selected agent profile"),
                ("p", "Preview identity snippet"),
            ],
        ),
        (
            "Graph tab  (Ctrl+0)",
            [
                ("/  (search)", "Focus third-brain search"),
                ("r", "Refresh provider status and refreshable sources"),
            ],
        ),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-overlay"), Vertical(id="help-dialog"):
            yield Label("OpenMinion TUI — Keyboard Reference", id="help-title")
            for section, entries in self._resolved_sections():
                yield Label(section, classes="help-section")
                for key, desc in entries:
                    yield Label(f"  {key:<28} {desc}", classes="help-row")
            yield Label("")
            yield Label("Press Esc / q / F1 to close", classes="help-row")

    def _resolved_sections(self) -> list[tuple[str, list[tuple[str, str]]]]:
        return [
            (
                section,
                self._chat_command_entries() if section == "Chat commands" else entries,
            )
            for section, entries in self.SECTIONS
        ]
