from __future__ import annotations

import pytest

from openminion.cli.tui.app import OpenMinionApp


@pytest.mark.asyncio
async def test_sessions_detail_renders_room_participants() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-sessions")
        await pilot.pause()

        await pilot.click("#sess-row-sess-abc123")
        await pilot.pause()

        detail = str(app.screen.query_one(".session-detail-text").render())
        assert "Participants:" in detail
        assert "[human/owner] owner" in detail
        assert "[agent/participant] default" in detail
