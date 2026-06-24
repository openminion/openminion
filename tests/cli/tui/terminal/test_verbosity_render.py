from __future__ import annotations

import io

from rich.console import Console

from openminion.cli.tui.terminal.streaming import (
    _TOOL_BLOCK_VERBOSE_MAX_LINES,
)
from openminion.cli.tui.terminal.transcript import TerminalTranscript
from openminion.cli.tui.presentation.models import (
    ChatMessage,
    MessageKind,
    ToolEvent,
)


def _make_transcript(
    verbosity: str = "normal",
) -> tuple[TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    return TerminalTranscript(console, verbosity=verbosity), buf


def _push_long_tool(
    transcript: TerminalTranscript,
    *,
    tool_name: str,
    lines: int,
    exit_code: int = 0,
) -> None:
    body = "\n".join(f"{tool_name}-line {i}" for i in range(1, lines + 1))
    event = ToolEvent(
        tool_name=tool_name,
        args={"cmd": f"echo {tool_name}"},
        content=body,
        full_content=body,
        exit_code=exit_code,
    )
    transcript.push_message(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender=f"tool:{tool_name}",
            body="",
            tool_event=event,
        )
    )


def test_quiet_hides_tool_block_output() -> None:
    t, buf = _make_transcript("quiet")
    _push_long_tool(t, tool_name="Bash", lines=20)
    output = buf.getvalue()
    assert "Bash-line 1" not in output
    assert "Bash-line 20" not in output
    assert "●" not in output


def test_quiet_increments_hidden_counter() -> None:
    t, _ = _make_transcript("quiet")
    _push_long_tool(t, tool_name="Bash", lines=20)
    _push_long_tool(t, tool_name="Read", lines=3)
    _push_long_tool(t, tool_name="Grep", lines=8)
    assert t._hidden_tool_count == 3


def test_quiet_increments_failed_counter_only_on_failure() -> None:
    t, _ = _make_transcript("quiet")
    _push_long_tool(t, tool_name="Ok", lines=3, exit_code=0)
    _push_long_tool(t, tool_name="Failed", lines=3, exit_code=1)
    _push_long_tool(t, tool_name="AlsoFailed", lines=3, exit_code=137)
    assert t._hidden_tool_count == 3
    assert t._hidden_failed_count == 2


def test_quiet_zero_exit_code_does_not_count_as_failure() -> None:
    t, _ = _make_transcript("quiet")
    _push_long_tool(t, tool_name="Bash", lines=3, exit_code=0)
    assert t._hidden_failed_count == 0


def test_quiet_none_exit_code_does_not_count_as_failure() -> None:
    t, _ = _make_transcript("quiet")
    body = "x"
    event = ToolEvent(
        tool_name="Read",
        args={"path": "/etc/hosts"},
        content=body,
        full_content=body,
        exit_code=None,
    )
    t.push_message(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:Read",
            body="",
            tool_event=event,
        )
    )
    assert t._hidden_failed_count == 0


def test_quiet_tracks_blocks_for_expand_inspection() -> None:
    t, _ = _make_transcript("quiet")
    _push_long_tool(t, tool_name="Bash", lines=20)
    _push_long_tool(t, tool_name="Read", lines=20)
    assert len(t._truncated_blocks) == 2


def test_quiet_expand_block_re_renders_full_body() -> None:
    t, buf = _make_transcript("quiet")
    _push_long_tool(t, tool_name="Bash", lines=20)
    pre_len = len(buf.getvalue())
    assert t.expand_block(1) is True
    post_output = buf.getvalue()[pre_len:]
    # All 20 lines now visible (the full re-render).
    assert "Bash-line 1" in post_output
    assert "Bash-line 20" in post_output


def test_verbose_shows_full_50_line_body_no_summary() -> None:
    t, buf = _make_transcript("verbose")
    _push_long_tool(t, tool_name="Bash", lines=50)
    output = buf.getvalue()
    assert "Bash-line 1" in output
    assert "Bash-line 25" in output
    assert "Bash-line 50" in output
    assert "… +" not in output  # no truncation summary


def test_verbose_caps_at_200_lines_with_summary() -> None:
    t, buf = _make_transcript("verbose")
    _push_long_tool(t, tool_name="Bash", lines=300)
    output = buf.getvalue()
    assert "Bash-line 1" in output
    assert "Bash-line 200" in output
    assert "… +100 lines" in output
    assert "/expand" in output
    assert "Bash-line 201" not in output
    assert "Bash-line 300" not in output


def test_verbose_marker_still_present() -> None:
    t, buf = _make_transcript("verbose")
    _push_long_tool(t, tool_name="Bash", lines=10)
    output = buf.getvalue()
    assert "●" in output


def test_verbose_capped_block_lands_in_truncated_list() -> None:
    t, _ = _make_transcript("verbose")
    _push_long_tool(t, tool_name="Bash", lines=300)
    assert len(t._truncated_blocks) == 1


def test_verbose_under_cap_does_not_land_in_truncated_list() -> None:
    t, _ = _make_transcript("verbose")
    _push_long_tool(t, tool_name="Bash", lines=50)
    assert len(t._truncated_blocks) == 0


def test_verbose_expand_bypasses_cap() -> None:
    t, buf = _make_transcript("verbose")
    _push_long_tool(t, tool_name="Bash", lines=300)
    pre_len = len(buf.getvalue())
    t.expand_block(1)
    expanded = buf.getvalue()[pre_len:]
    assert "Bash-line 250" in expanded
    assert "Bash-line 300" in expanded
    assert "… +" not in expanded


def test_verbose_cap_constant_is_200() -> None:
    assert _TOOL_BLOCK_VERBOSE_MAX_LINES == 200


def test_normal_truncates_to_six_lines() -> None:
    t, buf = _make_transcript("normal")
    _push_long_tool(t, tool_name="Bash", lines=30)
    output = buf.getvalue()
    assert "Bash-line 1" in output
    assert "Bash-line 6" in output
    assert "Bash-line 7" not in output
    assert "… +24 lines" in output
    assert "/expand" in output


def test_normal_short_block_no_summary() -> None:
    t, buf = _make_transcript("normal")
    _push_long_tool(t, tool_name="Echo", lines=2)
    output = buf.getvalue()
    assert "Echo-line 1" in output
    assert "Echo-line 2" in output
    assert "… +" not in output


def test_normal_truncated_blocks_tracked() -> None:
    t, _ = _make_transcript("normal")
    _push_long_tool(t, tool_name="Bash", lines=20)
    _push_long_tool(t, tool_name="Echo", lines=3)  # not truncated
    assert len(t._truncated_blocks) == 1


def test_default_verbosity_is_normal() -> None:
    t, _ = _make_transcript()  # no verbosity arg
    assert t._verbosity == "normal"


def test_invalid_verbosity_falls_back_to_normal() -> None:
    t, _ = _make_transcript("loud")
    assert t._verbosity == "normal"


def test_transcript_accepts_verbosity_kwarg() -> None:
    import inspect

    sig = inspect.signature(TerminalTranscript.__init__)
    assert "verbosity" in sig.parameters
    assert sig.parameters["verbosity"].default == "normal"
