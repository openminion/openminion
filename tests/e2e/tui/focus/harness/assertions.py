from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_DONE_RE = re.compile(r"\bDone in \d+(?:m\d{2}s|s)\b")

_CRASH_MARKERS = (
    "Traceback (most recent call last)",
    "Fatal Python error",
    "openminion focus: error",
)

_RAW_TOOL_MARKERS = (
    "<minimax:tool_call>",
    "<functioncall>",
    "<invoke name=",
    "[tool_call]",
)


def visible_text(text: str) -> str:
    return _ANSI_RE.sub("", text)


def assert_no_terminal_crash(transcript: str) -> None:
    visible = visible_text(transcript)
    for marker in (*_CRASH_MARKERS, *_RAW_TOOL_MARKERS):
        assert marker not in visible, marker


def assert_focus_turn_completed(transcript: str) -> None:
    visible = visible_text(transcript)
    assert _DONE_RE.search(visible), visible[-2000:]
    assert_no_terminal_crash(visible)
