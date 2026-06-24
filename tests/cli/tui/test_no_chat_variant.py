from __future__ import annotations

import pytest
from textual.widgets import TabbedContent

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION, ProviderBundle
from openminion.cli.tui.app import OpenMinionApp
from openminion.cli.tui.widgets import SidebarItem


class _NoChatRuntime:
    contract_version = CLI_INTERFACE_VERSION

    def __init__(self) -> None:
        self._agent_id = "nochat-agent"
        self._session_id = "sess-nochat"
        self._transport = "dashboard-only"

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def transport(self) -> str:
        return self._transport

    def get_current_history(self):
        return []

    def list_sessions(self):
        return [SidebarItem(self._session_id, self._session_id, active=True)]

    def list_agents(self):
        return [SidebarItem(self._agent_id, self._agent_id, active=True)]

    def list_tools(self):
        return [("weather", True)]

    def switch_session(self, session_id: str):
        self._session_id = session_id
        return []

    def switch_agent(self, agent_id: str) -> None:
        self._agent_id = str(agent_id or self._agent_id)

    def new_session(self) -> str:
        self._session_id = "sess-nochat-new"
        return self._session_id


@pytest.mark.asyncio
async def test_no_chat_variant_hides_chat_tab_and_ctrl1_binding() -> None:
    app = OpenMinionApp(runtime=_NoChatRuntime(), providers=ProviderBundle.all_demo())

    async with app.run_test() as pilot:
        await pilot.pause()

        tab_ids = [pane.id for pane in app.screen.query("TabPane")]
        assert "tab-chat" not in tab_ids

        bindings_text = " ".join(str(binding) for binding in app.screen.BINDINGS)
        assert "tab-chat" not in bindings_text


@pytest.mark.asyncio
async def test_no_chat_variant_chat_navigation_is_safe_no_op() -> None:
    app = OpenMinionApp(runtime=_NoChatRuntime(), providers=ProviderBundle.all_demo())

    async with app.run_test() as pilot:
        await pilot.pause()

        tabs = app.screen.query_one(TabbedContent)
        starting_tab = str(tabs.active)
        assert starting_tab == "tab-tasks"

        # Explicit chat navigation must be harmless in no-chat mode.
        app.screen.action_switch_tab("tab-chat")
        await pilot.pause()
        assert str(tabs.active) == starting_tab

        # Ctrl+1 is intentionally absent; key press should not switch to chat.
        await pilot.press("ctrl+1")
        await pilot.pause()
        assert str(tabs.active) == starting_tab
