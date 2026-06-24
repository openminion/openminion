from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from openminion.cli.tui.focus.widgets.mention_overlay import (
    RESULT_LIMIT,
    FileMentionOverlay,
)


class _Host(App):
    def compose(self) -> ComposeResult:
        yield FileMentionOverlay()


@pytest.mark.asyncio
async def test_set_items_seeds_unfiltered_list_capped_at_result_limit() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        overlay = app.query_one(FileMentionOverlay)
        items = [(f"file{i:04d}.py", f"/abs/file{i:04d}.py") for i in range(120)]
        overlay.set_items(items)
        await pilot.pause()

        # Query is empty — bare `@`-state. Filtered list should be
        # the first RESULT_LIMIT entries.
        assert len(overlay.filtered) == RESULT_LIMIT, len(overlay.filtered)
        assert overlay.filtered[0][0] == "file0000.py"
        assert overlay.filtered[-1][0] == f"file{RESULT_LIMIT - 1:04d}.py"


@pytest.mark.asyncio
async def test_query_narrows_to_prefix_then_substring() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        overlay = app.query_one(FileMentionOverlay)
        overlay.set_items(
            [
                ("alpha/widget.py", "/abs/alpha/widget.py"),
                ("src/widget.py", "/abs/src/widget.py"),
                ("widget.py", "/abs/widget.py"),
                ("zoo/other.py", "/abs/zoo/other.py"),
            ]
        )
        await pilot.pause()

        overlay.query = "@widget"
        await pilot.pause()

        names = [rel for rel, _ in overlay.filtered]
        # Tier-1 (prefix-matches `widget`) — only `widget.py` qualifies.
        # Tier-2 (substring `widget` anywhere) — `alpha/widget.py` and
        # `src/widget.py`. `zoo/other.py` doesn't match at all.
        assert names == [
            "widget.py",
            "alpha/widget.py",
            "src/widget.py",
        ], names


@pytest.mark.asyncio
async def test_selected_returns_highlighted_relative_path() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        overlay = app.query_one(FileMentionOverlay)
        overlay.set_items(
            [
                ("a.py", "/abs/a.py"),
                ("b.py", "/abs/b.py"),
                ("c.py", "/abs/c.py"),
            ]
        )
        await pilot.pause()

        # Initial highlight is row 0.
        assert overlay.selected() == "a.py"
        overlay.move_highlight(1)
        await pilot.pause()
        assert overlay.selected() == "b.py"
        overlay.move_highlight(1)
        await pilot.pause()
        assert overlay.selected() == "c.py"


@pytest.mark.asyncio
async def test_move_highlight_wraps_at_boundaries() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        overlay = app.query_one(FileMentionOverlay)
        overlay.set_items(
            [
                ("a.py", "/abs/a.py"),
                ("b.py", "/abs/b.py"),
            ]
        )
        await pilot.pause()

        assert overlay.highlighted_index == 0
        overlay.move_highlight(-1)
        await pilot.pause()
        # Wrap to last row.
        assert overlay.highlighted_index == 1
        overlay.move_highlight(1)
        await pilot.pause()
        # Wrap back to row 0.
        assert overlay.highlighted_index == 0


@pytest.mark.asyncio
async def test_visibility_toggle_applies_css_class() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        overlay = app.query_one(FileMentionOverlay)
        await pilot.pause()
        assert overlay.has_class("--visible") is False

        overlay.visible = True
        await pilot.pause()
        assert overlay.has_class("--visible") is True

        overlay.visible = False
        await pilot.pause()
        assert overlay.has_class("--visible") is False


@pytest.mark.asyncio
async def test_empty_filtered_list_renders_no_match_hint() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        overlay = app.query_one(FileMentionOverlay)
        overlay.set_items([("hello.py", "/abs/hello.py")])
        await pilot.pause()
        overlay.query = "@xyzzy-not-found"
        await pilot.pause()

        from textual.widgets import Label

        # Query through the app — the overlay's own `query` attribute
        # is a reactive (the active token) and shadows the DOM
        # `query()` method, so we go up one level.
        labels = list(app.query(Label))
        rendered = [str(getattr(lab, "content", "") or "") for lab in labels]
        assert any("no matching files" in text for text in rendered), rendered


@pytest.mark.asyncio
async def test_query_resets_highlight_to_zero() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        overlay = app.query_one(FileMentionOverlay)
        overlay.set_items(
            [
                ("alpha.py", "/abs/alpha.py"),
                ("beta.py", "/abs/beta.py"),
                ("gamma.py", "/abs/gamma.py"),
            ]
        )
        await pilot.pause()
        overlay.move_highlight(2)
        await pilot.pause()
        assert overlay.highlighted_index == 2

        # New query — highlight resets to 0.
        overlay.query = "@beta"
        await pilot.pause()
        assert overlay.highlighted_index == 0


@pytest.mark.asyncio
async def test_filter_is_case_insensitive() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        overlay = app.query_one(FileMentionOverlay)
        overlay.set_items(
            [("README.md", "/abs/README.md"), ("src/lib.py", "/abs/src/lib.py")]
        )
        await pilot.pause()

        overlay.query = "@readme"
        await pilot.pause()
        assert "README.md" in [rel for rel, _ in overlay.filtered]


@pytest.mark.asyncio
async def test_result_limit_constant_matches_spec() -> None:
    assert RESULT_LIMIT == 50
