from __future__ import annotations


from openminion.cli.tui.presentation.models import ToolEvent
from openminion.cli.tui.presentation.tool.blocks import ToolBlockWidget


# ── Collapse defaults ────────────────────────────────────────────────────────


def test_successful_tool_block_mounts_collapsed() -> None:
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "ls"},
        content="file1\nfile2\n",
        exit_code=0,
        duration_ms=42,
    )
    widget = ToolBlockWidget(event, pending=False)
    assert widget.collapsed is True


def test_failed_tool_block_mounts_expanded() -> None:
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "false"},
        content="(error output)",
        exit_code=1,
        duration_ms=12,
    )
    widget = ToolBlockWidget(event, pending=False)
    assert widget.collapsed is False, (
        "failed tool blocks must mount expanded so failures don't hide"
    )


def test_pending_tool_block_mounts_expanded() -> None:
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "long-running"},
        content="",
    )
    widget = ToolBlockWidget(event, pending=True)
    assert widget.collapsed is False
    # Title row carries the in-progress glyph.
    assert widget._header_text().startswith(ToolBlockWidget.EXIT_GLYPH_PENDING)


# ── Exit glyphs in title row ─────────────────────────────────────────────────


def test_title_row_starts_with_success_glyph() -> None:
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "echo hi"},
        content="hi",
        exit_code=0,
    )
    title = ToolBlockWidget(event)._header_text()
    assert title.startswith(ToolBlockWidget.EXIT_GLYPH_OK + " ")
    # title now uses the verb form (`Ran`) + hint, not the
    # raw `exec.run` type name.
    assert "Ran" in title
    assert "echo hi" in title


def test_title_row_starts_with_failure_glyph() -> None:
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "false"},
        content="",
        exit_code=2,
    )
    title = ToolBlockWidget(event)._header_text()
    assert title.startswith(ToolBlockWidget.EXIT_GLYPH_FAIL + " ")


# ── Pending → terminal transition ────────────────────────────────────────────


def test_pending_to_success_transition_collapses() -> None:
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "ls"},
        content="",
    )
    widget = ToolBlockWidget(event, pending=True)
    assert widget.collapsed is False  # pending → expanded
    final = ToolEvent(
        tool_name="exec.run",
        args={"command": "ls"},
        content="file1\nfile2\n",
        exit_code=0,
        duration_ms=22,
    )
    widget.update_event(final, pending=False)
    assert widget.collapsed is True


def test_pending_to_failure_transition_stays_expanded() -> None:
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "false"},
        content="",
    )
    widget = ToolBlockWidget(event, pending=True)
    final = ToolEvent(
        tool_name="exec.run",
        args={"command": "false"},
        content="boom",
        exit_code=1,
        duration_ms=15,
    )
    widget.update_event(final, pending=False)
    assert widget.collapsed is False


# ── Args summary truncation ──────────────────────────────────────────────────


def test_long_args_summary_truncates_to_60_chars() -> None:
    long_command = "echo " + ("x" * 200)
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": long_command},
        content="",
        exit_code=0,
    )
    title = ToolBlockWidget(event)._header_text()
    # Title contains the truncation ellipsis.
    assert "…" in title
    # The truncated hint is at most 60 chars (helper enforces this).
    truncated = ToolBlockWidget._truncate_hint(long_command)
    assert len(truncated) <= 60


def test_short_args_summary_not_truncated() -> None:
    short_command = "ls -la"
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": short_command},
        content="",
        exit_code=0,
    )
    title = ToolBlockWidget(event)._header_text()
    assert short_command in title
    assert "…" not in title
