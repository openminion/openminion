from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import OptionList

from openminion.cli.presentation import styles
from openminion.cli.theme import DARK
from openminion.cli.interactive.app import FocusApp, _DemoFocusRuntime
from openminion.cli.interactive.screen import FocusScreen
from openminion.cli.interactive.widgets import FocusTranscript, PermissionsOverlay
from openminion.cli.interactive.widgets.transcript import MessageKind
from openminion.services.runtime.turn_input import TurnInputQueueStatus


def _make_app(tmp: str) -> FocusApp:
    runtime = _DemoFocusRuntime(working_dir=tmp)
    return FocusApp(runtime=runtime, working_dir=tmp)


def _last_system_body(chat: FocusTranscript) -> str:
    for msg in reversed(chat._messages):
        if msg.kind == MessageKind.SYSTEM:
            return str(msg.body)
    return ""


@pytest.fixture(autouse=True)
def _restore_active_theme():
    original_codes = dict(styles._ANSI_CODES)
    original_name = styles.get_active_theme_name()
    styles.set_active_theme(DARK)
    yield
    styles._ANSI_CODES.clear()
    styles._ANSI_CODES.update(original_codes)
    styles._ACTIVE_THEME_NAME = original_name


@pytest.mark.asyncio
async def test_slash_help_lists_every_registered_command_with_description() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, FocusScreen)

            screen._handle_command("/help")
            await pilot.pause()
            chat = screen.query_one(FocusTranscript)
            body = _last_system_body(chat)

            for aliases, description, _handler in screen._slash_command_registry:
                primary = aliases[0]
                assert primary in body, (
                    f"`/help` body must mention command {primary!r}; got: {body!r}"
                )
                # First five chars of the description are enough to confirm
                # the row was rendered without forcing exact-string match.
                assert description[:6] in body, (
                    f"`/help` body must mention description for {primary!r}; "
                    f"got: {body!r}"
                )
            assert "Slash commands" in body
            # FCC-04 also surfaces a key-hint trailer for parity with
            # Claude-Code-style help output.
            assert "Ctrl+P" in body


@pytest.mark.asyncio
async def test_slash_permissions_bare_opens_focus_menu() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, FocusScreen)

            screen._handle_command("/permissions")
            await pilot.pause()

            assert isinstance(app.screen, PermissionsOverlay)
            option_list = app.screen.query_one(
                "#focus-permissions-overlay-list", OptionList
            )
            assert option_list.option_count == 4


@pytest.mark.asyncio
async def test_slash_queue_uses_shared_queue_vocabulary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, FocusScreen)
            runtime = screen._runtime
            screen._turn_input_queue.enqueue(
                session_id=runtime.session_id,
                agent_id=runtime.agent_id,
                text="queued follow-up prompt",
                source_client="test",
            )

            screen._handle_command("/queue")
            await pilot.pause()
            chat = screen.query_one(FocusTranscript)
            body = _last_system_body(chat)

            assert "Queued messages:" in body
            assert "1. queued follow-up prompt" in body
            assert "/queue drop <index>" in body

            screen._handle_command("/queue drop 1")
            await pilot.pause()

            assert _last_system_body(chat).startswith("Dropped queued message 1")
            remaining = screen._turn_input_queue.list_entries(
                session_id=runtime.session_id,
                agent_id=runtime.agent_id,
                statuses={TurnInputQueueStatus.QUEUED},
            )
            assert remaining == []


@pytest.mark.asyncio
async def test_permissions_menu_applies_ask_for_approval() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, FocusScreen)
            runtime = screen._runtime

            screen._handle_command("/permissions")
            await pilot.pause()
            overlay = app.screen
            assert isinstance(overlay, PermissionsOverlay)
            option_list = overlay.query_one(
                "#focus-permissions-overlay-list", OptionList
            )
            option_list.highlighted = 1
            await pilot.press("enter")
            await pilot.pause()

            assert isinstance(app.screen, FocusScreen)
            assert runtime.permission_mode == "default"
            assert runtime.action_policy_mode_override == "ask"
            body = _last_system_body(app.screen.query_one(FocusTranscript))
            assert body == "permissions → ask"


@pytest.mark.asyncio
async def test_permissions_menu_full_access_requires_second_selection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, FocusScreen)
            runtime = screen._runtime

            screen._handle_command("/permissions")
            await pilot.pause()
            overlay = app.screen
            assert isinstance(overlay, PermissionsOverlay)
            option_list = overlay.query_one(
                "#focus-permissions-overlay-list", OptionList
            )
            option_list.highlighted = 3
            await pilot.press("enter")
            await pilot.pause()

            assert isinstance(app.screen, PermissionsOverlay)
            assert runtime.permission_mode == "default"
            assert runtime.action_policy_mode_override == ""

            await pilot.press("enter")
            await pilot.pause()

            assert isinstance(app.screen, FocusScreen)
            assert runtime.permission_mode == "bypass"
            assert runtime.action_policy_mode_override == "bypass"
            body = _last_system_body(app.screen.query_one(FocusTranscript))
            assert "full access" in body


@pytest.mark.asyncio
async def test_shift_tab_action_opens_permissions_menu_instead_of_cycling() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, FocusScreen)
            runtime = screen._runtime

            screen.action_cycle_permission_mode()
            await pilot.pause()

            assert isinstance(app.screen, PermissionsOverlay)
            assert runtime.permission_mode == "default"


@pytest.mark.asyncio
async def test_slash_copy_routes_through_action_copy_last_agent() -> None:
    from openminion.cli.presentation.models import ChatMessage

    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            chat = screen.query_one(FocusTranscript)

            # Seed a copyable agent message so the copy action has a
            # target. Without one, `action_copy_last_agent` no-ops and
            # the user-visible notice doesn't fire.
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.AGENT,
                    sender="agent",
                    body="copy this token",
                )
            )
            await pilot.pause()

            screen._handle_command("/copy")
            await pilot.pause()

            body = _last_system_body(chat)
            assert "Copied" in body, (
                f"`/copy` should produce a Copied… notice; got: {body!r}"
            )


@pytest.mark.asyncio
async def test_slash_agent_bare_lists_known_agents_with_active_marker() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            chat = screen.query_one(FocusTranscript)
            active = str(getattr(screen._runtime, "agent_id", "") or "").strip()
            assert active, "demo runtime should expose an agent_id"

            screen._handle_command("/agent")
            await pilot.pause()
            body = _last_system_body(chat)

            assert "Agents:" in body, body
            assert active in body, body
            # Active marker '●' must appear adjacent to the active id.
            assert "●" in body, body
            assert "/agent <id>" in body, body


@pytest.mark.asyncio
async def test_slash_diff_surfaces_git_diff_output(monkeypatch) -> None:

    def _fake_render_git_diff(_working_dir, _args=""):
        return type(
            "Result",
            (),
            {"display_body": "diff --git a/note.txt b/note.txt\n+new"},
        )()

    monkeypatch.setattr(
        "openminion.cli.presentation.git.diff.render_git_diff",
        _fake_render_git_diff,
    )
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, FocusScreen)

            screen._handle_command("/diff note.txt")
            await pilot.pause()
            body = _last_system_body(screen.query_one(FocusTranscript))

            assert "diff --git" in body
            assert "+new" in body


@pytest.mark.asyncio
async def test_slash_agent_with_known_id_switches_runtime() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            runtime = screen._runtime
            agents = list(runtime.list_agents() or [])
            ids = [str(getattr(a, "id", a)).strip() for a in agents if a]
            target = next(
                (
                    aid
                    for aid in ids
                    if aid
                    and aid != str(getattr(runtime, "agent_id", "") or "").strip()
                ),
                None,
            )
            if target is None:
                pytest.skip("demo runtime only registers one agent — cannot switch")

            screen._handle_command(f"/agent {target}")
            await pilot.pause()

            assert str(getattr(runtime, "agent_id", "") or "").strip() == target
            chat = screen.query_one(FocusTranscript)
            body = _last_system_body(chat)
            assert "Switched to agent" in body and target in body, body


@pytest.mark.asyncio
async def test_slash_agent_unknown_id_rejects_without_crash() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            runtime = screen._runtime
            before_active = str(getattr(runtime, "agent_id", "") or "").strip()

            screen._handle_command("/agent does-not-exist-12345")
            await pilot.pause()

            after_active = str(getattr(runtime, "agent_id", "") or "").strip()
            assert before_active == after_active, (
                "unknown agent must NOT change the active agent"
            )
            chat = screen.query_one(FocusTranscript)
            body = _last_system_body(chat)
            assert "Unknown agent" in body, body


@pytest.mark.asyncio
async def test_unknown_slash_command_still_returns_unknown_message() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            chat = screen.query_one(FocusTranscript)

            screen._handle_command("/this-is-not-a-real-command")
            await pilot.pause()
            body = _last_system_body(chat)
            assert "Unknown command" in body, body


@pytest.mark.asyncio
async def test_existing_slash_commands_still_work_via_registry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            chat = screen.query_one(FocusTranscript)

            # `/clear` clears the chat view
            from openminion.cli.presentation.models import ChatMessage

            chat.push_message(ChatMessage(kind=MessageKind.AGENT, sender="a", body="x"))
            chat.push_message(ChatMessage(kind=MessageKind.AGENT, sender="a", body="y"))
            await pilot.pause()
            before = len(chat._messages)
            screen._handle_command("/clear")
            await pilot.pause()
            assert len(chat._messages) < before, (
                "/clear should drop messages — registry must dispatch to "
                "action_clear_screen"
            )

            # `/status` posts a multi-line status message
            screen._handle_command("/status")
            await pilot.pause()
            body = _last_system_body(chat)
            for needle in ("agent", "session", "dir"):
                assert needle in body, f"/status must include {needle!r}: {body!r}"


@pytest.mark.asyncio
async def test_slash_theme_lists_available_themes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            chat = screen.query_one(FocusTranscript)

            screen._handle_command("/theme list")
            await pilot.pause()

            body = _last_system_body(chat)
            assert "Available themes" in body, body
            assert "dark" in body and "light" in body, body


@pytest.mark.asyncio
async def test_slash_theme_switch_applies_focus_theme() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert app.active_theme.name == "dark"

            screen._handle_command("/theme light")
            await pilot.pause()

            assert app.active_theme.name == "light"
            chat = screen.query_one(FocusTranscript)
            body = _last_system_body(chat)
            assert "session-local" in body, body


@pytest.mark.asyncio
async def test_slash_theme_save_persists_to_data_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        app_runtime = getattr(app, "_runtime", None)
        if app_runtime is not None:
            app_runtime._rt = SimpleNamespace(
                data_root=tmp,
                config_path="/tmp/focus.json",
            )
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, FocusScreen)

            screen._handle_command("/theme save light")
            await pilot.pause()

            assert app.active_theme.name == "light"
            persisted = Path(tmp) / "cli" / "theme.json"
            assert persisted.exists()
            chat = screen.query_one(FocusTranscript)
            body = _last_system_body(chat)
            assert "theme saved to" in body.lower(), body


@pytest.mark.asyncio
async def test_slash_animation_status_use_save_reset_and_error(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("OPENMINION_DATA_ROOT", tmp)
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, FocusScreen)
            chat = screen.query_one(FocusTranscript)

            screen._handle_command("/animation")
            await pilot.pause()
            assert "active     openminion:braille" in _last_system_body(chat)

            screen._handle_command("/animation use openminion:braille")
            await pilot.pause()
            assert "animation → openminion:braille" in _last_system_body(chat)

            screen._handle_command("/animation save openminion:braille")
            await pilot.pause()
            persisted = Path(tmp) / "focus_prefs.toml"
            assert 'animation_provider = "openminion"' in persisted.read_text(
                encoding="utf-8"
            )
            assert "animation saved to" in _last_system_body(chat)

            screen._handle_command("/animation use missing:spinner")
            await pilot.pause()
            assert "/animation:" in _last_system_body(chat)
            assert screen._animation_label() == "openminion:braille"

            screen._handle_command("/animation reset")
            await pilot.pause()
            assert screen._animation_label() == "openminion:braille"


@pytest.mark.asyncio
async def test_slash_tasks_shows_task_inventory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, FocusScreen)

            screen._handle_command("/tasks")
            await pilot.pause()
            body = _last_system_body(screen.query_one(FocusTranscript))

            assert "Tasks" in body
