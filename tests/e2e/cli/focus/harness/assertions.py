from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_DONE_RE = re.compile(r"\bDone in \d+(?:m\d{2}s|s)\b")

_CRASH_MARKERS = (
    "Traceback (most recent call last)",
    "Fatal Python error",
    "openminion: error",
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
    "[y]es / [N]o / [a]lways:",
    "without reaching a final answer",
)


def visible_text(text: str) -> str:
    return _ANSI_RE.sub("", text)


def turn_output_text(transcript: str, prompt: str) -> str:
    visible = visible_text(transcript)
    prompt = prompt.strip()
    if prompt:
        output_frames: list[str] = []
        prompt_seen = False
        normalized_prompt = " ".join(prompt.split())
        for frame in visible.split("\f"):
            normalized_frame = " ".join(frame.split())
            prompt_index = normalized_frame.find(normalized_prompt)
            if prompt_index >= 0:
                prompt_seen = True
                output_frames.append(
                    normalized_frame[prompt_index + len(normalized_prompt) :]
                )
            elif prompt_seen:
                output_frames.append(normalized_frame)
        if prompt_seen:
            return "\n".join(output_frames)
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
    done_matches = list(_DONE_RE.finditer(visible))
    assert done_matches, visible[-2000:]
    assert_no_terminal_crash(visible)
    after_final_done = visible[done_matches[-1].end() :]
    for marker in _INCOMPLETE_TURN_MARKERS:
        assert marker not in after_final_done, marker


def assert_expected_markers(
    transcript: str, prompt: str, markers: tuple[str, ...]
) -> None:
    output = turn_output_text(transcript, prompt).lower()
    for marker in markers:
        alternatives = tuple(
            part.strip().lower() for part in marker.split("|") if part.strip()
        )
        assert any(alternative in output for alternative in alternatives), marker
