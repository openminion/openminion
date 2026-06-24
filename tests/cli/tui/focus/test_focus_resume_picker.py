from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from openminion.cli.presentation import styles
from openminion.cli.theme import DARK
from openminion.cli.tui.focus.app import FocusApp, _DemoFocusRuntime
from openminion.cli.tui.focus.widgets.resume_picker import (
    ResumePickerScreen,
    _ResumePickerRow,
    build_resume_dicts,
    relative_age,
)


@pytest.fixture(autouse=True)
def _restore_active_theme():
    original_codes = dict(styles._ANSI_CODES)
    original_name = styles.get_active_theme_name()
    styles.set_active_theme(DARK)
    yield
    styles._ANSI_CODES.clear()
    styles._ANSI_CODES.update(original_codes)
    styles._ACTIVE_THEME_NAME = original_name


def test_relative_age_seconds() -> None:
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
    earlier = now - timedelta(seconds=30)
    assert relative_age(earlier.isoformat(), now=now) == "30s ago"


def test_relative_age_minutes() -> None:
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
    earlier = now - timedelta(minutes=5)
    assert relative_age(earlier.isoformat(), now=now) == "5m ago"


def test_relative_age_hours() -> None:
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
    earlier = now - timedelta(hours=2, minutes=30)
    assert relative_age(earlier.isoformat(), now=now) == "2h ago"


def test_relative_age_days() -> None:
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
    earlier = now - timedelta(days=3, hours=1)
    assert relative_age(earlier.isoformat(), now=now) == "3d ago"


def test_relative_age_handles_z_suffix() -> None:
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
    earlier = now - timedelta(minutes=10)
    iso_z = earlier.isoformat().replace("+00:00", "Z")
    assert relative_age(iso_z, now=now) == "10m ago"


def test_relative_age_handles_empty_input() -> None:
    assert relative_age("") == ""
    assert relative_age("not a timestamp") == ""


def test_build_resume_dicts_from_simple_namespace() -> None:
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
    sessions = [
        SimpleNamespace(
            id="sess-a",
            name="alpha",
            updated_at=(now - timedelta(minutes=15)).isoformat(),
            message_count=8,
            preview_line="hello world",
        ),
        SimpleNamespace(
            id="sess-b",
            name="",
            updated_at=(now - timedelta(hours=2)).isoformat(),
            message_count=12,
            preview_line="",
        ),
    ]
    dicts = build_resume_dicts(sessions, now=now)
    assert len(dicts) == 2
    assert dicts[0]["id"] == "sess-a"
    assert dicts[0]["name"] == "alpha"
    assert dicts[0]["age"] == "15m ago"
    assert dicts[0]["message_count"] == 8
    assert dicts[0]["preview_line"] == "hello world"
    assert dicts[1]["age"] == "2h ago"


def test_build_resume_dicts_skips_records_without_id() -> None:
    sessions = [
        SimpleNamespace(id="", name="ghost", message_count=5),
        SimpleNamespace(id="sess-a", name="real", message_count=2),
    ]
    dicts = build_resume_dicts(sessions)
    assert len(dicts) == 1
    assert dicts[0]["id"] == "sess-a"


def test_build_resume_dicts_falls_back_to_turn_count_for_message_count() -> None:
    sessions = [
        SimpleNamespace(id="sess-a", turn_count=4),
        SimpleNamespace(id="sess-b"),  # neither field
    ]
    dicts = build_resume_dicts(sessions)
    assert dicts[0]["message_count"] == 4
    assert dicts[1]["message_count"] == 0


def test_resume_row_includes_age_count_and_preview() -> None:
    row = _ResumePickerRow(
        {
            "id": "sess-foo",
            "name": "demo",
            "age": "2h ago",
            "message_count": 5,
            "preview_line": "Hello, agent!",
        },
        index=0,
    )
    rendered = str(row.render())
    assert "demo" in rendered
    assert "2h ago" in rendered
    assert "5 msgs" in rendered
    assert "Hello, agent!" in rendered


def test_resume_row_singular_count() -> None:
    row = _ResumePickerRow(
        {"id": "sess-x", "name": "one-msg", "message_count": 1},
        index=0,
    )
    assert "1 msg" in str(row.render())
    assert "1 msgs" not in str(row.render())


def test_resume_row_truncates_long_preview() -> None:
    long_preview = "x" * 200
    row = _ResumePickerRow(
        {"id": "sess-x", "name": "long", "preview_line": long_preview},
        index=0,
    )
    rendered = str(row.render())
    assert "…" in rendered
    assert long_preview not in rendered


@pytest.mark.asyncio
async def test_resume_picker_mounts_with_sessions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="picker-mount-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            picker = ResumePickerScreen(
                [
                    {
                        "id": "sess-a",
                        "name": "alpha",
                        "age": "5m ago",
                        "message_count": 3,
                        "preview_line": "first message",
                    },
                ]
            )
            app.push_screen(picker)
            await pilot.pause()
            from textual.widgets import Label

            title = picker.query_one("#picker-title", Label)
            assert "Resume a session" in str(title.render())
            rows = list(picker.query(_ResumePickerRow))
            assert len(rows) == 1


@pytest.mark.asyncio
async def test_slash_resume_opens_resume_picker_screen() -> None:
    pushed: list = []

    class _SessionsRuntime(_DemoFocusRuntime):
        def list_directory_sessions(self, *, limit: int = 20):
            del limit
            return [
                SimpleNamespace(id="sess-real", name="real", message_count=3),
            ]

    with tempfile.TemporaryDirectory() as tmp:
        runtime = _SessionsRuntime(working_dir=tmp, session="resume-pick-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            original_push = app.push_screen

            def _record_push(s, *args, **kwargs):
                pushed.append(s)
                return original_push(s, *args, **kwargs)

            app.push_screen = _record_push  # type: ignore[method-assign]
            screen._handle_command("/resume")
            await pilot.pause()
    resume_screens = [s for s in pushed if isinstance(s, ResumePickerScreen)]
    assert resume_screens, (
        f"/resume should push ResumePickerScreen; pushed: "
        f"{[type(s).__name__ for s in pushed]}"
    )


@pytest.mark.asyncio
async def test_slash_resume_falls_back_when_lister_missing() -> None:
    fallback_called = []

    class _NoListerRuntime(_DemoFocusRuntime):
        list_directory_sessions = None  # type: ignore[assignment]

    with tempfile.TemporaryDirectory() as tmp:
        runtime = _NoListerRuntime(working_dir=tmp, session="resume-fallback-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            original = screen.action_show_sessions
            screen.action_show_sessions = lambda: fallback_called.append(True)  # type: ignore[method-assign]
            screen._handle_command("/resume")
            await pilot.pause()
            screen.action_show_sessions = original  # type: ignore[method-assign]
    assert fallback_called == [True]
