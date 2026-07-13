from __future__ import annotations

import pytest
from types import SimpleNamespace
from textual.css.query import QueryError

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION, ProviderBundle
from openminion.cli.tui.app import (
    DemoCronProvider,
    DemoMemoryProvider,
    DemoPolicyProvider,
    DemoSessionsProvider,
    DemoTasksProvider,
    OpenMinionApp,
    _MockApprovalStore,
)
from openminion.cli.tui.tabs.agents import AgentsTab
from openminion.cli.tui.tabs.cron import CronTab
from openminion.cli.tui.tabs.memory import MemoryTab
from openminion.cli.tui.tabs.policy import PolicyTab
from openminion.cli.tui.tabs.sessions import SessionsTab
from openminion.cli.tui.tabs.system import SystemTab
from openminion.cli.tui.tabs.tasks import TasksTab
from openminion.cli.tui.widgets import SidebarItem
from textual.widgets import DataTable


class _SpySystemProvider:
    contract_version = CLI_INTERFACE_VERSION

    def __init__(self) -> None:
        self.calls = 0

    def get_daemon_status(self) -> dict:
        self.calls += 1
        return {"mode": "demo", "endpoint": "—", "pid": "—", "uptime": "1m"}

    def get_agent_info(self) -> dict:
        self.calls += 1
        return {
            "model": "demo-model",
            "runtime_mode": "brain",
            "brain_mode": "planner",
            "provider": "demo",
        }

    def get_storage_stats(self) -> dict:
        self.calls += 1
        return {
            "db_size": "1 MB",
            "session_count": 3,
            "event_count": 9,
            "memory_count": 2,
        }

    def get_telemetry_summary(self) -> dict:
        self.calls += 1
        return {"turns": 3, "tool_calls": 2, "errors": 0, "avg_latency": "1.2s"}

    def get_plugin_status(self) -> list[dict]:
        self.calls += 1
        return [{"name": "demo", "enabled": True}]


class _DegradedRuntime:
    contract_version = CLI_INTERFACE_VERSION

    def __init__(self) -> None:
        self._agent_id = "degraded-agent"
        self._session_id = "sess-degraded"
        self._transport = "in-process"

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def transport(self) -> str:
        return self._transport

    async def send_message(self, text: str, *, progress_callback=None):
        del progress_callback
        yield f"echo: {text}"

    def get_current_history(self) -> list:
        return []

    def list_sessions(self) -> list[SidebarItem]:
        return [SidebarItem(self._session_id, self._session_id, active=True)]

    def list_agents(self) -> list[SidebarItem]:
        return [SidebarItem(self._agent_id, self._agent_id, active=True)]

    def list_tools(self) -> list[tuple[str, bool]]:
        return [("weather", True)]

    def switch_session(self, session_id: str) -> list:
        self._session_id = session_id
        return []

    def switch_agent(self, agent_id: str) -> None:
        self._agent_id = agent_id

    def new_session(self) -> str:
        self._session_id = "sess-degraded-2"
        return self._session_id


def test_dashboard_tabs_sync_layout_mode_ignore_missing_bodies() -> None:
    class _MissingBody:
        app = SimpleNamespace(size=SimpleNamespace(width=80))

        def query_one(self, *_args, **_kwargs):
            raise QueryError("missing body")

    stub = _MissingBody()

    for cls in (
        MemoryTab,
        TasksTab,
        CronTab,
        SessionsTab,
        SystemTab,
        PolicyTab,
        AgentsTab,
    ):
        cls._sync_layout_mode(stub)


@pytest.mark.asyncio
async def test_main_screen_mounts_expected_tabs_and_context_footer_hints() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        tab_ids = [pane.id for pane in app.screen.query("TabPane")]
        assert tab_ids == [
            "tab-chat",
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

        footer_hints = app.screen.query_one("#footer-context-hints")
        chat_hint = str(footer_hints.render())
        assert "^B sidebar" in chat_hint
        assert "^P palette" not in chat_hint
        assert "^D debug" not in chat_hint

        app.screen.action_switch_tab("tab-sessions")
        await pilot.pause()
        sessions_hint = str(footer_hints.render())
        assert "search" in sessions_hint
        assert "resume" in sessions_hint

        app.screen.action_switch_tab("tab-tasks")
        await pilot.pause()
        tasks_hint = str(footer_hints.render())
        assert "a/d" in tasks_hint

        app.screen.action_switch_tab("tab-cron")
        await pilot.pause()
        cron_hint = str(footer_hints.render())
        assert "toggle" in cron_hint.lower() or "enable" in cron_hint.lower()

        app.screen.action_switch_tab("tab-monitor")
        await pilot.pause()
        monitor_hint = str(footer_hints.render())
        assert "refresh" in monitor_hint.lower()

        app.screen.action_switch_tab("tab-third-brain")
        await pilot.pause()
        graph_hint = str(footer_hints.render())
        assert "search" in graph_hint.lower()
        assert "copy" in graph_hint.lower()


@pytest.mark.asyncio
async def test_tasks_approval_resolution_routes_policy_refresh_via_main_screen() -> (
    None
):
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-tasks")
        await pilot.pause()

        await pilot.click("#task-task-003")
        await pilot.pause()
        assert len(app.screen.query("#task-detail .pending-action")) == 1

        await pilot.click("#pending-dec-001")
        await pilot.press("a")
        await pilot.pause()

        assert len(app.screen.query("#task-detail .pending-action")) == 0

        app.screen.action_switch_tab("tab-policy")
        await pilot.pause()

        policy_tab = app.screen.query_one(PolicyTab)
        assert len(policy_tab._pending) == 0
        assert len(policy_tab._grants) == 1

        history_rows = app.screen.query("#policy-right .history-row")
        assert len(history_rows) >= 1
        assert "exec" in str(history_rows[0].render())
        assert "allow" in str(history_rows[0].render())


@pytest.mark.asyncio
async def test_policy_pending_decision_renders_two_line_layout() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-policy")
        await pilot.pause()

        pending_rows = app.screen.query("#policy-left .policy-row")
        assert len(pending_rows) >= 1
        rendered = str(pending_rows[0].render())
        assert "\n" in rendered
        assert "[pending]" in rendered


@pytest.mark.asyncio
async def test_policy_grant_revoke_button_refreshes_list() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-policy")
        await pilot.pause()

        assert len(app.screen.query("#policy-left .grant-row")) == 1

        app.screen.query_one("#revoke-grant-001").press()
        await pilot.pause()
        app.screen.query_one("#grant-confirm-ok").press()
        await pilot.pause()

        assert len(app.screen.query("#policy-left .grant-row")) == 0


@pytest.mark.asyncio
async def test_system_tab_manual_refresh_action_hits_provider_again() -> None:
    approval_store = _MockApprovalStore()
    system_provider = _SpySystemProvider()
    app = OpenMinionApp(
        providers=ProviderBundle(
            tasks=DemoTasksProvider(approval_store),
            cron=DemoCronProvider(),
            sessions=DemoSessionsProvider(),
            system=system_provider,
            policy=DemoPolicyProvider(approval_store),
            memory=DemoMemoryProvider(),
        )
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-system")
        await pilot.pause()

        initial_calls = system_provider.calls
        await pilot.press("r")
        await pilot.pause()

        assert system_provider.calls > initial_calls


@pytest.mark.asyncio
async def test_main_screen_footer_badges_include_cron_sessions_and_memory_counts() -> (
    None
):
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        cron_label = str(app.screen.query_one("#footer-tab-cron").label)
        sessions_label = str(app.screen.query_one("#footer-tab-sessions").label)
        memory_label = str(app.screen.query_one("#footer-tab-memory").label)
        graph_label = str(app.screen.query_one("#footer-tab-third-brain").label)

        assert "Cron(2)" in cron_label
        assert "Sess(4)" in sessions_label
        assert "Mem(3)" in memory_label
        assert "Graph" in graph_label


@pytest.mark.asyncio
async def test_command_palette_filters_and_switches_tabs() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_command_palette()
        await pilot.pause()

        palette_input = app.screen.query_one("#palette-input")
        palette_input.value = "agents"
        await pilot.pause()

        option_list = app.screen.query_one("#palette-list")
        assert option_list.option_count == 1
        option_list.focus()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        main_screen = next(
            screen
            for screen in app.screen_stack
            if screen.__class__.__name__ == "MainScreen"
        )
        tabs = main_screen.query_one("TabbedContent")
        assert str(tabs.active) == "tab-agents"


@pytest.mark.asyncio
async def test_help_screen_chat_commands_are_sourced_from_shared_registry(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "openminion.cli.tui.screen.slash_help_rows",
        lambda: [
            ("/custom", "custom help text from shared registry"),
        ],
    )

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_show_help()
        await pilot.pause()

        help_rows = [str(node.render()) for node in app.screen.query(".help-row")]
        assert any("/custom" in row for row in help_rows)
        assert any("custom help text from shared registry" in row for row in help_rows)


@pytest.mark.asyncio
async def test_degraded_runtime_shows_banner_and_warning_status() -> None:
    app = OpenMinionApp(runtime=_DegradedRuntime(), no_picker=True)
    async with app.run_test() as pilot:
        await pilot.pause()

        banner = app.screen.query_one("#runtime-banner")
        banner_text = app.screen.query_one("#runtime-banner-text")
        status_dot = app.screen.query_one("#header-status-dot")

        assert "Running without daemon" in str(banner_text.render())
        assert status_dot.has_class("--warning")

        await pilot.press("escape")
        await pilot.pause()
        assert banner.has_class("--hidden")


@pytest.mark.asyncio
async def test_debug_pane_toggle_and_filters_runtime_events() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.screen.query_one("#debug-pane")
        assert pane.has_class("--hidden")

        app.screen.action_toggle_debug()
        await pilot.pause()
        await pilot.pause()

        assert not pane.has_class("--hidden")
        table = app.screen.query_one("#debug-events", DataTable)
        assert table.row_count >= 1

        app.screen.query_one("#debug-filter-tool").press()
        await pilot.pause()
        filtered = app.screen.query_one("#debug-events", DataTable)
        assert filtered.row_count == 2


@pytest.mark.asyncio
async def test_two_panel_tabs_stack_at_narrow_width_and_restore_when_wide() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        # Every two-pane tab — including tasks/cron/memory which were
        # audit; the inconsistency made tasks/cron/memory look broken at
        # 80 cols while sibling tabs reflowed correctly.
        narrow_tabs = (
            ("tab-tasks", "#tasks-body"),
            ("tab-cron", "#cron-body"),
            ("tab-sessions", "#sessions-body"),
            ("tab-system", "#system-body"),
            ("tab-policy", "#policy-body"),
            ("tab-memory", "#memory-body"),
            ("tab-agents", "#agents-body"),
            ("tab-third-brain", "#third-brain-body"),
        )
        for tab_id, body_id in narrow_tabs:
            app.screen.action_switch_tab(tab_id)
            await pilot.pause()
            await pilot.pause()
            assert app.screen.query_one(body_id).has_class("--stacked"), (
                f"{tab_id} must apply --stacked at narrow widths"
            )

        await pilot.resize_terminal(120, 40)
        await pilot.pause()
        await pilot.pause()

        for tab_id, body_id in narrow_tabs:
            app.screen.action_switch_tab(tab_id)
            await pilot.pause()
            await pilot.pause()
            assert not app.screen.query_one(body_id).has_class("--stacked"), (
                f"{tab_id} must drop --stacked at wide widths"
            )


@pytest.mark.asyncio
async def test_ctrl_p_opens_custom_command_palette_not_textual_builtin() -> None:
    from openminion.cli.tui.screen import CommandPaletteScreen, MainScreen

    assert OpenMinionApp.ENABLE_COMMAND_PALETTE is False

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, MainScreen)
        await pilot.press("ctrl+p")
        await pilot.pause()
        assert isinstance(app.screen, CommandPaletteScreen), (
            f"ctrl+p should open our CommandPaletteScreen, "
            f"got {type(app.screen).__name__}"
        )


@pytest.mark.asyncio
async def test_sidebar_section_headings_show_live_counts() -> None:
    from openminion.cli.tui.widgets import Sidebar

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sidebar = app.screen.query_one(Sidebar)
        # Demo seeds at least one session + agent, and a handful of tools.
        assert sidebar.sessions, "demo sidebar should seed sessions"
        assert sidebar.agents, "demo sidebar should seed agents"
        # Collect heading text
        headings = [
            str(getattr(lab, "content", "") or lab.render())
            for lab in sidebar.query(".sidebar-heading")
        ]
        expected_sessions = f"SESSIONS ({len(sidebar.sessions)})"
        expected_agents = f"AGENTS ({len(sidebar.agents)})"
        assert any(h.startswith(expected_sessions) for h in headings), (
            f"expected heading {expected_sessions!r} in {headings}"
        )
        assert any(h.startswith(expected_agents) for h in headings), (
            f"expected heading {expected_agents!r} in {headings}"
        )
        # Tool heading uses "TOOLS (n)" or "TOOLS (enabled/total)" form
        assert any(h.startswith("TOOLS") and "(" in h for h in headings), (
            f"expected tools heading with count in {headings}"
        )


def test_sidebar_show_preview_ignores_missing_preview_widget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.cli.tui.widgets import Sidebar

    sidebar = Sidebar()

    def _raise_query_error(*args, **kwargs):
        raise QueryError("missing preview")

    monkeypatch.setattr(sidebar, "query_one", _raise_query_error)
    sidebar.show_preview("preview body")


@pytest.mark.asyncio
async def test_policy_revoke_modal_closes_on_escape() -> None:
    from openminion.cli.tui.screen import MainScreen
    from openminion.cli.tui.tabs.policy import _ConfirmGrantRevokeModal
    from textual.widgets import Button

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+6")
        await pilot.pause()
        btns = [
            b for b in app.screen.query(Button) if (b.id or "").startswith("revoke-")
        ]
        if not btns:
            pytest.skip("demo policy provider did not seed a grant")
        btns[0].press()
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, _ConfirmGrantRevokeModal)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, MainScreen), (
            "revoke confirm modal must dismiss on escape"
        )


@pytest.mark.asyncio
async def test_slash_help_opens_help_screen_without_crash() -> None:
    from openminion.cli.tui.screen import HelpScreen, MainScreen
    from openminion.cli.tui.tabs import ChatTab

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        tab = app.screen.query_one(ChatTab)
        tab._handle_command("/help")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen), (
            f"/help must open HelpScreen, got {type(app.screen).__name__}"
        )
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, MainScreen)


@pytest.mark.asyncio
async def test_every_slash_command_survives_dispatch() -> None:
    from openminion.cli.tui.screen import MainScreen
    from openminion.cli.tui.tabs import ChatTab

    commands = [
        "/help",
        "/mcp",
        "/tools",
        "/status",
        "/debug",
        "/sidebar",
        "/trust",
        "/untrust",
        "/grants",
        "/artifacts",
        "/identity",
        "/skill",
        "/sidecar",
        "/pair",
        "/new",
        "/clear",
    ]

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        tab = app.screen.query_one(ChatTab)
        for cmd in commands:
            try:
                tab._handle_command(cmd)
                await pilot.pause()
            except Exception as exc:
                pytest.fail(f"{cmd} raised {exc!r}")
            # Back out of any modal the command may have opened
            for _ in range(5):
                if isinstance(app.screen, MainScreen):
                    break
                await pilot.press("escape")
                await pilot.pause()


@pytest.mark.asyncio
async def test_command_palette_enter_on_input_selects_entry() -> None:
    from openminion.cli.tui.screen import CommandPaletteScreen, MainScreen
    from textual.widgets import TabbedContent

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p")
        await pilot.pause()
        assert isinstance(app.screen, CommandPaletteScreen)
        for char in "tasks":
            await pilot.press(char)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, MainScreen), (
            f"palette must close on Enter; still on {type(app.screen).__name__}"
        )
        tabs = app.screen.query_one(TabbedContent)
        assert str(tabs.active) == "tab-tasks", (
            f"palette Enter should navigate to filtered tab; active={tabs.active}"
        )


@pytest.mark.asyncio
async def test_agents_e_binding_opens_edit_modal() -> None:
    from openminion.cli.tui.tabs.agents import AgentsTab, _EditProfileModal

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+9")
        await pilot.pause()
        tab = app.screen.query_one(AgentsTab)
        tab._selected_agent_id = "alibaba-minimax"
        await tab._async_refresh()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, _EditProfileModal), (
            f"`e` should open edit modal, got {type(app.screen).__name__}"
        )


@pytest.mark.asyncio
async def test_agents_e_without_selection_notifies_instead_of_opening_modal() -> None:
    from openminion.cli.tui.tabs.agents import AgentsTab, _EditProfileModal

    notifications: list[str] = []
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+9")
        await pilot.pause()
        tab = app.screen.query_one(AgentsTab)
        tab._selected_agent_id = None
        tab._detail = {}

        original_notify = app.notify

        def capture(message, **kwargs):
            notifications.append(str(message))
            return original_notify(message, **kwargs)

        app.notify = capture  # type: ignore[method-assign]

        await pilot.press("e")
        await pilot.pause()
        assert not isinstance(app.screen, _EditProfileModal)
    assert any("edit" in n.lower() for n in notifications), (
        f"`e` without selection should raise a toast; got {notifications}"
    )


@pytest.mark.asyncio
async def test_help_screen_closes_on_escape_q_and_f1() -> None:
    from openminion.cli.tui.screen import HelpScreen, MainScreen

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        for close_key in ("escape", "q", "f1"):
            await pilot.press("f1")
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen), "F1 should open help"
            await pilot.press(close_key)
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, MainScreen), (
                f"help must close on {close_key!r}; still on "
                f"{type(app.screen).__name__}"
            )


@pytest.mark.asyncio
async def test_chat_tab_shell_bindings_fire_when_input_focused() -> None:
    from openminion.cli.tui.widgets import ChatInputBar, ChatSearchBar
    from openminion.cli.tui.widgets.input_bar import ChatInput

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.focused, ChatInput), (
            "baseline: chat input auto-focuses on mount"
        )

        # Ctrl+F opens the search bar without input eating the key
        search = app.screen.query_one(ChatSearchBar)
        assert not search.display
        await pilot.press("ctrl+f")
        await pilot.pause()
        assert search.display, "ctrl+f should open search even with input focused"
        await pilot.press("escape")
        await pilot.pause()
        assert not search.display

        # Ctrl+L toggles multiline (sanity: this binding never conflicted
        # with Input, should still work)
        bar = app.screen.query_one(ChatInputBar)
        before = bool(getattr(bar, "_multiline", False))
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert bool(getattr(bar, "_multiline", False)) != before


@pytest.mark.asyncio
async def test_chat_input_strips_shell_reserved_keys() -> None:
    from openminion.cli.tui.widgets.input_bar import ChatInput

    reserved = {"ctrl+d", "ctrl+k", "ctrl+f", "ctrl+a"}
    leaked = []
    for binding in ChatInput.BINDINGS:
        for key in binding.key.split(","):
            if key.strip() in reserved:
                leaked.append((key.strip(), binding.action))
    assert not leaked, (
        f"ChatInput BINDINGS must not keep shell-reserved keys; leaked={leaked}"
    )


@pytest.mark.asyncio
async def test_keyboard_tab_switch_routes_focus_into_active_tab() -> None:
    from openminion.cli.tui.tabs import (
        AgentsTab,
        CronTab,
        MemoryTab,
        MonitorTab,
        PolicyTab,
        SessionsTab,
        SystemTab,
        ThirdBrainTab,
        TasksTab,
    )

    cases = [
        ("ctrl+2", TasksTab),
        ("ctrl+3", CronTab),
        ("ctrl+4", SessionsTab),
        ("ctrl+5", SystemTab),
        ("ctrl+6", PolicyTab),
        ("ctrl+7", MemoryTab),
        ("ctrl+8", MonitorTab),
        ("ctrl+9", AgentsTab),
        ("ctrl+0", ThirdBrainTab),
    ]

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        for key, cls in cases:
            await pilot.press(key)
            await pilot.pause()
            assert isinstance(app.focused, cls), (
                f"pressing {key} should focus {cls.__name__}, "
                f"got {type(app.focused).__name__ if app.focused else None}"
            )


@pytest.mark.asyncio
async def test_cron_e_binding_toggles_selected_job_enabled_state() -> None:
    from openminion.cli.tui.tabs import CronTab

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+3")
        await pilot.pause()
        cron = app.screen.query_one(CronTab)
        assert cron._jobs, "cron demo provider should seed at least one job"
        first_id = str(cron._jobs[0].get("id", ""))
        cron._selected_job_id = first_id
        cron._sync_selected_job()
        before = bool(cron._jobs[0].get("enabled"))

        await pilot.press("e")
        await pilot.pause()
        # Refresh via provider reflects the new state
        after = bool(
            next(
                (job for job in cron._jobs if str(job.get("id", "")) == first_id),
                {},
            ).get("enabled")
        )
        assert before != after, (
            "cron `e` binding should flip the enabled flag on the selected job"
        )


@pytest.mark.asyncio
async def test_agents_tab_notifies_when_delete_or_preview_without_selection() -> None:
    from openminion.cli.tui.tabs.agents import AgentsTab

    notifications: list[str] = []

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+9")
        await pilot.pause()
        tab = app.screen.query_one(AgentsTab)
        tab._selected_agent_id = None

        original_notify = app.notify

        def capture(message, **kwargs):
            notifications.append(str(message))
            return original_notify(message, **kwargs)

        app.notify = capture  # type: ignore[method-assign]

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()

    assert any("delete" in n.lower() for n in notifications), (
        "pressing `d` without a selection should toast the user"
    )
    assert any("preview" in n.lower() for n in notifications), (
        "pressing `p` without a selection should toast the user"
    )


@pytest.mark.asyncio
async def test_system_tab_sidecar_buttons_update_status_and_consent() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-system")
        await pilot.pause()

        right_text = "\n".join(
            str(node.render()) for node in app.screen.query("#system-right Label")
        )
        assert "stopped" in right_text

        app.screen.query_one("#system-sidecar-toggle").press()
        await pilot.pause()
        right_text = "\n".join(
            str(node.render()) for node in app.screen.query("#system-right Label")
        )
        assert "running" in right_text

        app.screen.query_one("#system-sidecar-consent").press()
        await pilot.pause()
        right_text = "\n".join(
            str(node.render()) for node in app.screen.query("#system-right Label")
        )
        assert "denied" in right_text
