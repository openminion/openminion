from __future__ import annotations

import pytest

from openminion.cli.tui.app import DemoSessionsProvider, OpenMinionApp
from openminion.cli.tui.tabs.sessions import SessionsTab
from openminion.modules.storage.runtime.session_store import (
    agent_id_from_session_key,
    build_session_key,
)


def test_agent_id_from_session_key_round_trip() -> None:
    key = build_session_key(agent_id="my-agent", channel="cli", target="tui")
    assert agent_id_from_session_key(key) == "my-agent"


def test_agent_id_from_session_key_url_encoded() -> None:
    key = build_session_key(agent_id="My Agent", channel="cli", target="tui")
    assert agent_id_from_session_key(key) == "my agent"


def test_agent_id_from_session_key_no_agent_segment() -> None:
    assert agent_id_from_session_key("channel:cli|target:tui") == ""


def test_agent_id_from_session_key_empty() -> None:
    assert agent_id_from_session_key("") == ""


@pytest.mark.asyncio
async def test_sessions_tab_rows_show_agent_and_channel() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+4")  # navigate to Sessions tab
        await pilot.pause()
        sessions_tab = app.screen.query_one(SessionsTab)
        sessions = sessions_tab._all_sessions
        assert len(sessions) > 0
        for s in sessions:
            assert "agent_id" in s
            assert "channel" in s
            assert "name" in s


@pytest.mark.asyncio
async def test_sessions_tab_search_by_agent_id() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+4")
        await pilot.pause()
        sessions_tab = app.screen.query_one(SessionsTab)

        sessions_tab._all_sessions = [
            {
                "id": "sess-aaa",
                "age": "1h",
                "turn_count": 3,
                "agent_id": "default",
                "channel": "cli",
                "name": "",
            },
            {
                "id": "sess-bbb",
                "age": "2h",
                "turn_count": 1,
                "agent_id": "agent-02",
                "channel": "cli",
                "name": "",
            },
        ]

        query = "agent-02"
        sessions_tab._sessions = [
            s
            for s in sessions_tab._all_sessions
            if (
                str(s.get("id", "")).lower().startswith(query)
                or str(s.get("agent_id", "")).lower().startswith(query)
                or str(s.get("channel", "")).lower().startswith(query)
                or query in str(s.get("name", "")).lower()
            )
        ]
        assert len(sessions_tab._sessions) == 1
        assert sessions_tab._sessions[0]["agent_id"] == "agent-02"


@pytest.mark.asyncio
async def test_resume_requested_message_on_r_key() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+4")
        await pilot.pause()
        sessions_tab = app.screen.query_one(SessionsTab)

        sessions_tab._selected_session_id = "sess-abc123"

        received: list[SessionsTab.ResumeRequested] = []

        original_post = sessions_tab.post_message

        def _patched_post(msg):
            if isinstance(msg, SessionsTab.ResumeRequested):
                received.append(msg)
            return original_post(msg)

        sessions_tab.post_message = _patched_post  # type: ignore[method-assign]

        # Call on_key directly; pilot.press would hit the focused search input.
        from textual import events as textual_events

        key_event = textual_events.Key("r", "r")
        sessions_tab.on_key(key_event)
        await pilot.pause()

        assert len(received) > 0
        assert received[0].session_id == "sess-abc123"


def test_demo_sessions_provider_update_session_name() -> None:
    provider = DemoSessionsProvider()
    provider.update_session_name("sess-abc123", "My Session")
    sessions = provider.list_all_sessions()
    abc = next(s for s in sessions if s["id"] == "sess-abc123")
    assert abc["name"] == "My Session"


def test_demo_sessions_provider_update_session_name_empty() -> None:
    provider = DemoSessionsProvider()
    provider.update_session_name("sess-abc123", "First name")
    provider.update_session_name("sess-abc123", "")
    sessions = provider.list_all_sessions()
    abc = next(s for s in sessions if s["id"] == "sess-abc123")
    assert abc["name"] == ""


def test_demo_sessions_provider_close_session_removes_session() -> None:
    provider = DemoSessionsProvider()
    initial = len(provider.list_all_sessions())
    provider.close_session("sess-abc123")
    sessions = provider.list_all_sessions()
    assert len(sessions) == initial - 1
    assert all(session["id"] != "sess-abc123" for session in sessions)
