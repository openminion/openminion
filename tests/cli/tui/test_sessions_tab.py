from __future__ import annotations

import pytest

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION, ProviderBundle
from openminion.cli.tui.app import (
    DemoCronProvider,
    DemoMemoryProvider,
    DemoPolicyProvider,
    DemoSystemProvider,
    DemoTasksProvider,
    OpenMinionApp,
    _MockApprovalStore,
)
from textual.widgets import Input


class _SpySessionsProvider:
    contract_version = CLI_INTERFACE_VERSION

    def __init__(self) -> None:
        self.list_calls = 0
        self.timeline_calls = 0
        self.delete_calls: list[str] = []
        self._sessions = [
            {"id": "sess-abc123", "age": "2h", "turn_count": 12},
            {"id": "sess-def456", "age": "1d", "turn_count": 4},
            {"id": "sess-ghi789", "age": "3d", "turn_count": 28},
        ]

    def list_all_sessions(self) -> list[dict]:
        self.list_calls += 1
        return list(self._sessions)

    def get_session_timeline(self, session_id: str) -> list[dict]:
        self.timeline_calls += 1
        return [
            {"ts": "10:21", "event_type": "llm.call.started", "detail": session_id},
            {"ts": "10:22", "event_type": "llm.call.completed", "detail": "ok"},
        ]

    def close_session(self, session_id: str) -> None:
        return None

    def delete_session(self, session_id: str) -> None:
        self.delete_calls.append(session_id)
        self._sessions = [
            session for session in self._sessions if session.get("id") != session_id
        ]

    def update_session_name(self, session_id: str, name: str) -> None:
        return None


@pytest.mark.asyncio
async def test_sessions_click_and_search_filter_without_provider_calls() -> None:
    approval_store = _MockApprovalStore()
    sessions_provider = _SpySessionsProvider()
    app = OpenMinionApp(
        providers=ProviderBundle(
            tasks=DemoTasksProvider(approval_store),
            cron=DemoCronProvider(),
            sessions=sessions_provider,
            system=DemoSystemProvider(),
            policy=DemoPolicyProvider(approval_store),
            memory=DemoMemoryProvider(),
        )
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-sessions")
        await pilot.pause()

        initial_rows = app.screen.query("#sessions-list .session-row")
        assert len(initial_rows) == 3
        assert sessions_provider.list_calls == 1
        assert sessions_provider.timeline_calls == 0
        assert app.screen.query_one("#sess-row-sess-abc123").has_class(
            "session-age-normal"
        )
        assert app.screen.query_one("#sess-row-sess-def456").has_class(
            "session-age-normal"
        )
        assert app.screen.query_one("#sess-row-sess-ghi789").has_class(
            "session-age-stale"
        )

        await pilot.press("/")
        await pilot.pause()
        assert app.screen.query_one("#sessions-search", Input).has_focus

        await pilot.click("#sess-row-sess-abc123")
        await pilot.pause()
        assert len(app.screen.query("#sessions-list .session-row.selected")) == 1
        assert sessions_provider.timeline_calls == 1
        assert len(app.screen.query("#sessions-timeline .event-row")) == 2

        search = app.screen.query_one("#sessions-search", Input)
        search.value = "sess-def"
        await pilot.pause()

        filtered_rows = app.screen.query("#sessions-list .session-row")
        assert len(filtered_rows) == 1
        assert app.screen.query_one("#sess-row-sess-def456")
        assert len(app.screen.query("#sessions-list .session-row.selected")) == 0
        assert "Select a session to view its timeline" in str(
            app.screen.query_one("#sessions-timeline .dim-hint").render()
        )

        # Search must be instant in-memory over cached sessions.
        assert sessions_provider.list_calls == 1
        assert sessions_provider.timeline_calls == 1


@pytest.mark.asyncio
async def test_sessions_timeline_filter_buttons_reduce_visible_events() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-sessions")
        await pilot.pause()

        await pilot.click("#sess-row-sess-abc123")
        await pilot.pause()

        assert len(app.screen.query("#sessions-timeline .event-row")) >= 5

        await pilot.click("#timeline-filter-tool")
        await pilot.pause()
        filtered_rows = app.screen.query("#sessions-timeline .event-row")
        assert len(filtered_rows) == 2
        assert all("tool." in str(row.render()) for row in filtered_rows)

        await pilot.click("#timeline-filter-memory")
        await pilot.pause()
        filtered_rows = app.screen.query("#sessions-timeline .event-row")
        assert len(filtered_rows) == 1
        assert "memory." in str(filtered_rows[0].render())


@pytest.mark.asyncio
async def test_sessions_delete_modal_removes_selected_session_and_refreshes() -> None:
    approval_store = _MockApprovalStore()
    sessions_provider = _SpySessionsProvider()
    app = OpenMinionApp(
        providers=ProviderBundle(
            tasks=DemoTasksProvider(approval_store),
            cron=DemoCronProvider(),
            sessions=sessions_provider,
            system=DemoSystemProvider(),
            policy=DemoPolicyProvider(approval_store),
            memory=DemoMemoryProvider(),
        )
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_switch_tab("tab-sessions")
        await pilot.pause()

        await pilot.click("#sess-row-sess-abc123")
        await pilot.pause()
        app.screen.query_one("#sessions-tab").action_delete_session()
        await pilot.pause()
        app.screen.query_one("#delete-session-confirm").press()
        await pilot.pause()

        assert sessions_provider.delete_calls == ["sess-abc123"]
        assert not app.screen.query("#sess-row-sess-abc123")
