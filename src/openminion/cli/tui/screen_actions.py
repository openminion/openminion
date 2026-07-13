# mypy: disable-error-code="attr-defined"

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events
from textual.css.query import QueryError
from textual.widgets import TabbedContent

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

if TYPE_CHECKING:
    from .screen import FooterBar


class MainScreenActionsMixin:
    def on_footer_bar_tab_switch(self, event: "FooterBar.TabSwitch") -> None:
        self.action_switch_tab(event.tab_id)

    def on_sessions_tab_resume_requested(
        self, event: SessionsTab.ResumeRequested
    ) -> None:
        from .screen import TAB_CHAT

        if not self._has_chat:
            return
        tabs = self.query_one(TabbedContent)
        tabs.active = TAB_CHAT
        chat_tab = self.query_one(ChatTab)
        chat_tab._do_switch_session(event.session_id)

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        from .screen import FooterBar

        tab_id = ""
        pane = getattr(event, "pane", None)
        if pane is not None and getattr(pane, "id", None):
            tab_id = str(pane.id)
        if not tab_id:
            tab_id = str(self.query_one(TabbedContent).active)
        from openminion.cli.status.surface import record_surface_event

        record_surface_event(
            self._runtime,
            surface="dashboard",
            action="tab",
            tab=tab_id,
        )
        self.query_one(FooterBar).set_context_hints(tab_id)
        self._refresh_tab_badges()
        self.call_after_refresh(self._focus_active_tab, tab_id)

    def _focus_active_tab(self, tab_id: str) -> None:
        from .screen import TAB_CHAT

        if not tab_id or tab_id == TAB_CHAT:
            return
        tab_cls = {
            "tab-tasks": TasksTab,
            "tab-cron": CronTab,
            "tab-sessions": SessionsTab,
            "tab-system": SystemTab,
            "tab-policy": PolicyTab,
            "tab-memory": MemoryTab,
            "tab-monitor": MonitorTab,
            "tab-agents": AgentsTab,
            "tab-third-brain": ThirdBrainTab,
        }.get(tab_id)
        if tab_cls is None:
            return
        try:
            tab_widget = self.query_one(tab_cls)
        except (QueryError, AttributeError):
            return
        try:
            tab_widget.focus()
        except (QueryError, AttributeError):
            pass

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            try:
                from .screen import RuntimeBanner

                banner = self.query_one(RuntimeBanner)
            except (ImportError, QueryError, AttributeError):
                banner = None
            if banner is not None and banner.is_visible:
                banner.dismiss()
                event.stop()
                return
        active_tab = str(self.query_one(TabbedContent).active)
        if event.key != "r":
            return
        if not self._refresh_active_tab(active_tab):
            return
        event.stop()

    def _refresh_active_tab(self, tab_id: str) -> bool:
        if tab_id == "tab-system":
            self.query_one(SystemTab).action_refresh()
            return True
        if tab_id == "tab-monitor":
            self.query_one(MonitorTab).action_refresh()
            return True
        if tab_id == "tab-third-brain":
            self.query_one(ThirdBrainTab).action_refresh()
            return True
        worker_tabs = {
            "tab-tasks": TasksTab,
            "tab-cron": CronTab,
            "tab-policy": PolicyTab,
            "tab-agents": AgentsTab,
        }
        tab_cls = worker_tabs.get(tab_id)
        if tab_cls is None:
            return False
        self.run_worker(self.query_one(tab_cls).action_refresh())
        return True

    def action_switch_tab(self, tab_id: str) -> None:
        from .screen import FooterBar, TAB_CHAT

        if tab_id == TAB_CHAT and not self._has_chat:
            return
        tabs = self.query_one(TabbedContent)
        tabs.active = tab_id
        self.query_one(FooterBar).set_context_hints(tab_id)

    def action_show_help(self) -> None:
        from .screen import HelpScreen

        self.app.push_screen(HelpScreen())

    def action_focus_tab_search(self) -> None:
        active_tab = str(self.query_one(TabbedContent).active)
        if active_tab == "tab-sessions":
            self.query_one(SessionsTab).action_focus_search()
        elif active_tab == "tab-memory":
            self.query_one(MemoryTab).action_focus_search()
        elif active_tab == "tab-third-brain":
            self.query_one(ThirdBrainTab).action_focus_search()

    def action_command_palette(self) -> None:
        def _on_result(result: str | None) -> None:
            if result is None:
                return
            if result.startswith("tab-"):
                self.action_switch_tab(result)
            elif result.startswith("cmd-"):
                cmd_name = result.removeprefix("cmd-")
                if cmd_name == "exit":
                    self.app.exit()
                elif cmd_name == "help":
                    self.action_show_help()
                elif self._has_chat:
                    chat_tab = self.query_one(ChatTab)
                    chat_tab._handle_command(f"/{cmd_name}")

        from .screen import CommandPaletteScreen

        self.app.push_screen(CommandPaletteScreen(), _on_result)

    def action_toggle_debug(self) -> None:
        from .screen import DebugPane

        pane = self.query_one(DebugPane)
        pane.toggle()
