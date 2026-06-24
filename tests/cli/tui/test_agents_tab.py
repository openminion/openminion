from __future__ import annotations

import pytest

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION, ProviderBundle
from openminion.cli.tui.app import OpenMinionApp


def _rendered_text(widget) -> str:
    return str(widget.render())


def _detail_text(app: OpenMinionApp) -> str:
    parts = [
        _rendered_text(widget)
        for widget in app.screen.query("#agents-detail Label, #agents-detail Button")
    ]
    return "\n".join(part for part in parts if part)


def _agent_row_text(app: OpenMinionApp, row_id: str) -> str:
    parts = [_rendered_text(widget) for widget in app.screen.query(f"#{row_id} Label")]
    return "\n".join(part for part in parts if part)


@pytest.mark.asyncio
async def test_agents_tab_mounts_agent_list() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-agents")
        await pilot.pause()

        rows = app.screen.query("#agents-list .agent-row")
        assert len(rows) >= 3
        assert app.screen.query_one("#agent-row-alibaba-minimax")
        assert app.screen.query_one("#agent-row-researcher")
        assert "Select an agent to view its profile" in _rendered_text(
            app.screen.query_one("#agents-detail .dim-hint")
        )


@pytest.mark.asyncio
async def test_agents_tab_click_agent_shows_detail() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-agents")
        await pilot.pause()

        await pilot.click("#agent-row-researcher")
        await pilot.pause()

        rendered = _detail_text(app)
        assert "AGENT PROFILE" in rendered
        assert "Research Agent" in rendered
        assert "researcher" in rendered
        assert app.screen.query_one("#agents-switch-btn")


@pytest.mark.asyncio
async def test_agents_tab_new_agent_modal_creates_entry() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-agents")
        await pilot.pause()

        await pilot.click("#agents-new-btn")
        await pilot.pause()

        agent_id = app.screen.query_one("#new-agent-id")
        display_name = app.screen.query_one("#new-agent-name")
        agent_id.value = "ops-safe"
        display_name.value = "Ops Safe"
        await pilot.pause()

        await pilot.click("#new-agent-create")
        await pilot.pause()

        assert app.screen.query_one("#agent-row-ops-safe")
        detail = _detail_text(app)
        assert "Ops Safe" in detail
        assert "ops-safe" in detail


@pytest.mark.asyncio
async def test_agents_tab_edit_modal_updates_profile_fields() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-agents")
        await pilot.pause()

        await pilot.click("#agent-row-code-reviewer")
        await pilot.pause()
        app.screen.query_one("#agents-edit-btn").press()
        await pilot.pause()

        display_name = app.screen.query_one("#edit-display-name")
        mission = app.screen.query_one("#edit-mission")
        display_name.value = "Code Reviewer Pro"
        mission.value = "Review pull requests carefully."
        await pilot.pause()

        app.screen.query_one("#edit-save").press()
        await pilot.pause()

        assert "Code Reviewer Pro" in _detail_text(app)
        assert "Code Reviewer Pro" in _agent_row_text(app, "agent-row-code-reviewer")


@pytest.mark.asyncio
async def test_agents_tab_delete_modal_removes_entry() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-agents")
        await pilot.pause()

        await pilot.click("#agent-row-researcher")
        await pilot.pause()
        app.screen.query_one("#agents-delete-btn").press()
        await pilot.pause()
        app.screen.query_one("#confirm-delete-yes").press()
        await pilot.pause()

        assert not app.screen.query("#agent-row-researcher")
        assert "Select an agent to view its profile" in _rendered_text(
            app.screen.query_one("#agents-detail .dim-hint")
        )


class _EmptyAgentsProvider:
    contract_version = CLI_INTERFACE_VERSION

    def list_agents(self) -> list[dict]:
        return []

    def get_agent_detail(self, agent_id: str) -> dict:
        return {"agent_id": agent_id}

    def get_agent_tools(self, agent_id: str) -> list[dict]:
        return []

    def upsert_profile(self, profile_dict: dict) -> str:
        return "v1"

    def delete_profile(self, agent_id: str) -> None:
        return None

    def create_default_profile(self, agent_id: str, display_name: str) -> dict:
        return {"agent_id": agent_id, "display_name": display_name or agent_id}

    def render_identity_preview(
        self, agent_id: str, *, purpose: str = "act", max_tokens: int = 256
    ) -> str:
        return ""


@pytest.mark.asyncio
async def test_agents_tab_empty_state_shows_creation_guidance() -> None:
    app = OpenMinionApp(providers=ProviderBundle(agents=_EmptyAgentsProvider()))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-agents")
        await pilot.pause()

        empty_hint = _rendered_text(app.screen.query_one("#agents-list .dim-hint"))
        assert "No agent profiles found" in empty_hint
        assert "Press `n` to create one" in empty_hint
        assert "openminion setup" in empty_hint


@pytest.mark.asyncio
async def test_agents_tab_preview_modal_renders_identity_preview() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-agents")
        await pilot.pause()

        await pilot.click("#agent-row-researcher")
        await pilot.pause()
        agents_tab = app.screen.query_one("#tab-agents AgentsTab")
        agents_tab.action_preview_identity()
        await pilot.pause()

        preview_screen = app.screen
        preview_title = _rendered_text(preview_screen.query_one(".modal-title"))
        preview_body = _rendered_text(
            preview_screen.query_one("#identity-preview-text")
        )
        assert "Identity Preview: researcher" in preview_title
        assert "Mission" in preview_body
        assert "A helpful assistant." in preview_body
