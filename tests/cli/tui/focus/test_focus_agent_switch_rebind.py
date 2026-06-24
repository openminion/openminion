from __future__ import annotations

import tempfile

import pytest

from openminion.cli.tui.focus.app import FocusApp, _DemoFocusRuntime
from openminion.cli.tui.focus.widgets import FocusTranscript
from openminion.cli.tui.focus.widgets.transcript import ChatMessage, MessageKind


def _make_app(tmp: str) -> FocusApp:
    runtime = _DemoFocusRuntime(working_dir=tmp)
    return FocusApp(runtime=runtime, working_dir=tmp)


@pytest.mark.asyncio
async def test_slash_agent_rebinds_session_after_switch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            runtime = screen._runtime
            agents = list(runtime.list_agents() or [])
            ids = [str(getattr(a, "id", a)).strip() for a in agents if a]
            current = str(getattr(runtime, "agent_id", "") or "").strip()
            target = next((aid for aid in ids if aid and aid != current), None)
            if target is None:
                pytest.skip("demo runtime only registers one agent")

            screen._handle_command(f"/agent {target}")
            await pilot.pause()
            await pilot.pause()  # let any post-switch worker settle

            assert str(getattr(runtime, "agent_id", "") or "").strip() == target
            session_id = str(getattr(runtime, "session_id", "") or "").strip()
            assert session_id, (
                "/agent <id> must leave the runtime bound to a session "
                "(session_id non-empty); got empty after switch"
            )


@pytest.mark.asyncio
async def test_slash_agent_clears_stale_transcript_on_switch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            chat = screen.query_one(FocusTranscript)
            sentinel = "ALPHA-AGENT-PRIVATE-TRANSCRIPT-EE70"
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.AGENT,
                    sender="alpha",
                    body=sentinel,
                )
            )
            await pilot.pause()

            runtime = screen._runtime
            agents = list(runtime.list_agents() or [])
            ids = [str(getattr(a, "id", a)).strip() for a in agents if a]
            current = str(getattr(runtime, "agent_id", "") or "").strip()
            target = next((aid for aid in ids if aid and aid != current), None)
            if target is None:
                pytest.skip("demo runtime only registers one agent")

            screen._handle_command(f"/agent {target}")
            await pilot.pause()
            await pilot.pause()

            bodies = [str(m.body) for m in chat._messages]
            assert sentinel not in "\n".join(bodies), (
                "previous agent's chat transcript must be cleared on /agent switch"
            )


class _FocusAdapterDouble(_DemoFocusRuntime):
    def __init__(self, *, working_dir: str) -> None:
        super().__init__(working_dir=working_dir)
        self._post_switch_cleared = False
        self._post_switch_bind_id: str | None = None

    @property
    def is_bound(self) -> bool:
        return bool(str(self._session_id or "").strip())

    def find_candidate_session(self):
        return None

    def create_new_session(self) -> str:
        new_id = super().create_new_session()
        self._post_switch_bind_id = new_id
        return new_id

    def switch_agent(self, agent_id: str) -> None:
        super().switch_agent(agent_id)
        self._session_id = ""
        self._post_switch_cleared = True


@pytest.mark.asyncio
async def test_slash_agent_rebinds_when_real_adapter_clears_session() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _FocusAdapterDouble(working_dir=tmp)
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            agents = list(runtime.list_agents() or [])
            ids = [str(getattr(a, "id", a)).strip() for a in agents if a]
            current = str(getattr(runtime, "agent_id", "") or "").strip()
            target = next((aid for aid in ids if aid and aid != current), None)
            if target is None:
                pytest.skip("demo runtime only registers one agent")

            screen._handle_command(f"/agent {target}")
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

            assert runtime._post_switch_cleared, (
                "double should have cleared session on switch — sanity check"
            )
            session_id = str(getattr(runtime, "session_id", "") or "").strip()
            assert session_id, (
                "after switch, focus shell must rebind via initialize-session "
                "flow so session_id is non-empty"
            )


@pytest.mark.asyncio
async def test_slash_agent_still_posts_switched_notice_after_rebind() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            runtime = screen._runtime
            agents = list(runtime.list_agents() or [])
            ids = [str(getattr(a, "id", a)).strip() for a in agents if a]
            current = str(getattr(runtime, "agent_id", "") or "").strip()
            target = next((aid for aid in ids if aid and aid != current), None)
            if target is None:
                pytest.skip("demo runtime only registers one agent")

            screen._handle_command(f"/agent {target}")
            await pilot.pause()
            await pilot.pause()

            chat = screen.query_one(FocusTranscript)
            joined = "\n".join(
                str(m.body) for m in chat._messages if m.kind == MessageKind.SYSTEM
            )
            assert "Switched to agent" in joined and target in joined
