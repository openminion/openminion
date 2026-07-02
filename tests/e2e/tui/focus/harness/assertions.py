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

_INCOMPLETE_TURN_MARKERS = (
    "Policy confirmation required.",
    "Reply exactly yes to confirm",
    "without reaching a final answer",
)


def visible_text(text: str) -> str:
    return _ANSI_RE.sub("", text)


def turn_output_text(transcript: str, prompt: str) -> str:
    visible = visible_text(transcript)
    prompt = prompt.strip()
    if prompt:
        prompt_index = visible.rfind(prompt)
        if prompt_index >= 0:
            return visible[prompt_index + len(prompt) :]
    prompt_index = visible.rfind("❯")
    if prompt_index >= 0:
        line_end = visible.find("\n", prompt_index)
        if line_end >= 0:
            return visible[line_end + 1 :]
    return visible


def assert_no_terminal_crash(transcript: str) -> None:
    visible = visible_text(transcript)
    for marker in (*_CRASH_MARKERS, *_RAW_TOOL_MARKERS):
        assert marker not in visible, marker


def assert_focus_turn_completed(transcript: str) -> None:
    visible = visible_text(transcript)
    assert _DONE_RE.search(visible), visible[-2000:]
    assert_no_terminal_crash(visible)
    for marker in _INCOMPLETE_TURN_MARKERS:
        assert marker not in visible, marker


def assert_expected_markers(
    transcript: str, prompt: str, markers: tuple[str, ...]
) -> None:
    output = turn_output_text(transcript, prompt).lower()
    for marker in markers:
        assert marker.lower() in output, marker
