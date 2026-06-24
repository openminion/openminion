from __future__ import annotations

import time

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from openminion.cli.presentation import styles as cli_styles
from openminion.cli.status.controller import PhaseStatusController
from openminion.cli.status.models import PhaseStatusViewModel
from openminion.cli.tui.presentation import status as presentation_status
from openminion.cli.tui.presentation.status import (
    DEFAULT_THINKING_LABEL,
    ThinkingIndicator,
)


def test_spinner_frames_are_shared_object() -> None:
    assert presentation_status._SPINNER_FRAMES is cli_styles._SPINNER_FRAMES
    assert presentation_status._SPINNER is cli_styles._SPINNER_FRAMES


class _IndicatorHarness(App):
    def __init__(self) -> None:
        super().__init__()
        self.indicator = ThinkingIndicator()

    def compose(self) -> ComposeResult:
        yield self.indicator


@pytest.mark.asyncio
async def test_view_model_push_renders_chat_cli_composite() -> None:
    view = PhaseStatusViewModel(
        status_key="working",
        primary_text="Working...",
        elapsed_text="1.5s",
        mode_label=None,
        tool_name=None,
        show_spinner=True,
        terminal=False,
        signature=(),
    )
    app = _IndicatorHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.indicator.is_thinking = True
        app.indicator.view_model = view
        await pilot.pause()
        rendered = str(app.indicator.query_one("#thinking-label", Label).render())
    assert "1.5s | Working..." in rendered, (
        f"expected chat-CLI composite in rendered label, got {rendered!r}"
    )


@pytest.mark.asyncio
async def test_view_model_push_mirrors_legacy_reactives() -> None:
    view = PhaseStatusViewModel(
        status_key="planning",
        primary_text="Planning steps…",
        elapsed_text="3.0s",
        mode_label=None,
        tool_name=None,
        show_spinner=True,
        terminal=False,
        signature=(),
    )
    app = _IndicatorHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.indicator.is_thinking = True
        app.indicator.view_model = view
        await pilot.pause()
        assert app.indicator.status_label == "Planning steps…"
        assert app.indicator.elapsed_text == "3.0s"


@pytest.mark.asyncio
async def test_legacy_reactive_path_still_renders_unified_format() -> None:
    app = _IndicatorHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.indicator.is_thinking = True
        app.indicator.status_label = "Planning steps…"
        app.indicator.elapsed_text = "2.5s"
        await pilot.pause()
        rendered = str(app.indicator.query_one("#thinking-label", Label).render())
    assert "2.5s | Planning steps…" in rendered, (
        f"legacy reactives must produce unified format, got {rendered!r}"
    )


@pytest.mark.asyncio
async def test_legacy_reactive_without_elapsed_renders_label_only() -> None:
    app = _IndicatorHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.indicator.is_thinking = True
        app.indicator.status_label = "Initializing"
        app.indicator.elapsed_text = ""
        await pilot.pause()
        rendered = str(app.indicator.query_one("#thinking-label", Label).render())
    assert "Initializing" in rendered
    assert "|" not in rendered


@pytest.mark.asyncio
async def test_elapsed_refresh_repaints_between_progress_events() -> None:
    view = PhaseStatusViewModel(
        status_key="working",
        primary_text="Working...",
        elapsed_text="1.0s",
        mode_label=None,
        tool_name=None,
        show_spinner=True,
        terminal=False,
        signature=(),
    )
    app = _IndicatorHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.indicator.is_thinking = True
        app.indicator.view_model = view
        await pilot.pause()
        first = str(app.indicator.query_one("#thinking-label", Label).render())
        assert "1.0s" in first
        app.indicator.elapsed_text = "2.5s"
        await pilot.pause()
        second = str(app.indicator.query_one("#thinking-label", Label).render())
        assert "2.5s" in second
        assert "1.0s" not in second.split("Working")[0]


def test_refresh_view_with_live_elapsed_splices_in_elapsed() -> None:
    controller = PhaseStatusController(fallback_label="thinking…")
    controller.start_turn()
    time.sleep(0.05)
    raw_view = PhaseStatusViewModel(
        status_key="working",
        primary_text="Working...",
        elapsed_text=None,  # what `update()` would produce
        mode_label=None,
        tool_name=None,
        show_spinner=True,
        terminal=False,
        signature=(),
    )
    refreshed = controller.refresh_view_with_live_elapsed(raw_view)
    assert refreshed is not raw_view
    assert raw_view.elapsed_text is None
    assert refreshed.elapsed_text is not None
    assert len(refreshed.elapsed_text) > 0
    assert "|" in refreshed.display_label


def test_refresh_view_with_live_elapsed_returns_input_when_no_turn() -> None:
    controller = PhaseStatusController(fallback_label="thinking…")
    raw_view = PhaseStatusViewModel(
        status_key="working",
        primary_text="Working...",
        elapsed_text=None,
        mode_label=None,
        tool_name=None,
        show_spinner=True,
        terminal=False,
        signature=(),
    )
    refreshed = controller.refresh_view_with_live_elapsed(raw_view)
    assert refreshed is raw_view


@pytest.mark.asyncio
async def test_view_model_cleared_at_turn_start_and_end() -> None:
    view = PhaseStatusViewModel(
        status_key="working",
        primary_text="Working...",
        elapsed_text="1.5s",
        mode_label=None,
        tool_name=None,
        show_spinner=True,
        terminal=False,
        signature=(),
    )
    app = _IndicatorHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.indicator.is_thinking = True
        app.indicator.view_model = view
        await pilot.pause()
        assert app.indicator.view_model is view
        app.indicator.is_thinking = False
        await pilot.pause()
        assert app.indicator.view_model is None
        app.indicator.is_thinking = True
        await pilot.pause()
        assert app.indicator.view_model is None
        assert app.indicator.status_label == DEFAULT_THINKING_LABEL
