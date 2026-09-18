from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta

from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService

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

_FAILED_FINAL_ANSWER_MARKERS = (
    "did not produce a usable synthesized answer",
    "ended without the required typed finalization_status contract",
    "required typed finalization_status contract",
    "empty_provider_response:",
    "llm error:",
)


def visible_text(text: str) -> str:
    return _ANSI_RE.sub("", text)


def final_answer_text(transcript: str, prompt: str) -> str:
    """Extract the completed answer from the latest rendered current-turn frame."""
    frames = [frame for frame in visible_text(transcript).split("\f") if frame.strip()]
    assert frames, "missing terminal frame"
    frame = frames[-1]
    normalized_prompt = " ".join(prompt.split())
    prompt_pattern = r"\s+".join(re.escape(word) for word in normalized_prompt.split())
    submitted = list(re.finditer(rf"(?m)^\s*[❯>◆]\s+{prompt_pattern}(?=\s|$)", frame))
    assert normalized_prompt and submitted, "current prompt missing from frame"
    output = frame[submitted[-1].end() :]
    done_matches = list(_DONE_RE.finditer(output))
    assert done_matches, "current turn completion missing from frame"
    output = output[: done_matches[-1].start()]
    answer_markers = list(re.finditer(r"(?m)^[ \t]*●(?:[ \t]|$)", output))
    assert answer_markers, "current assistant answer missing from frame"
    return " ".join(output[answer_markers[-1].end() :].split())


def assert_exact_reply(transcript: str, prompt: str, expected: str) -> None:
    answer = final_answer_text(transcript, prompt)
    assert answer == expected, f"expected exact reply {expected!r}, received {answer!r}"


def assert_recorded_answer(
    events: list[TelemetryEvent], *, session_id: str, answer: str
) -> None:
    recorded = [
        event.data["content"]
        for event in events
        if event.session_id == session_id and event.event_type == "turn.assistant"
    ]
    assert recorded, "current assistant output was not recorded"
    assert " ".join(recorded[-1].split()) == answer, (
        "terminal answer differs from recorded assistant output"
    )


def read_session_events(
    environment: dict[str, str], session_id: str
) -> list[TelemetryEvent]:
    service = TelemetryService(env=environment, read_only=True)
    try:
        return asyncio.run(service.get_session_summary(session_id)).events
    finally:
        service.close_sync()


def current_turn_events(
    events: list[TelemetryEvent], previous_ids: set[str]
) -> tuple[list[TelemetryEvent], str]:
    events = [event for event in events if event.event_id not in previous_ids]
    scopes = {
        event.turn_id
        for event in events
        if event.event_type == "agent.invocation.started"
    }
    assert len(scopes) == 1, "missing or ambiguous current invocation"
    return events, scopes.pop()


def assert_time_only_tools(
    events: list[TelemetryEvent], *, session_id: str, turn_scope_id: str
) -> None:
    current_events = [
        event
        for event in events
        if event.session_id == session_id
        and event.turn_id == turn_scope_id
        and event.data.get("turn_scope_id") == turn_scope_id
    ]
    requests = {
        event.data["call_id"]: event.data
        for event in current_events
        if event.event_type == "tool.call.requested" and event.data.get("call_id")
    }
    for event in current_events:
        if (
            event.event_type != "tool.call.completed"
            or event.data.get("status") != "success"
        ):
            continue
        request = requests.get(event.data.get("call_id"))
        assert request is not None, "successful tool result has no current request"
        name = request.get("canonical_name")
        if name == "tool.request":
            name = request.get("sanitized_normalized_arguments", {}).get("name")
        assert name in {"time.now", "time.in_zone"}, (
            f"unexpected successful tool: {name}"
        )


def _utc_instant(timestamp: str) -> datetime:
    assert "T" in timestamp and timestamp.endswith(("Z", "+00:00")), (
        f"expected ISO-8601 UTC timestamp, received {timestamp!r}"
    )
    instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    assert instant.utcoffset() == timedelta(0), timestamp
    fraction = re.search(r"[.,](\d+)", timestamp)
    assert fraction is None or not fraction.group(1)[6:].strip("0"), (
        "timestamp has nonzero sub-microsecond precision"
    )
    return instant


def assert_current_time_reply(
    transcript: str,
    prompt: str,
    events: list[TelemetryEvent],
    *,
    session_id: str,
    turn_scope_id: str,
) -> None:
    assert_time_only_tools(events, session_id=session_id, turn_scope_id=turn_scope_id)
    answer = _utc_instant(final_answer_text(transcript, prompt))
    assert turn_scope_id, "missing current turn scope"
    current_events = [
        event
        for event in events
        if event.session_id == session_id
        and event.turn_id == turn_scope_id
        and event.data.get("turn_scope_id") == turn_scope_id
    ]
    requested = {
        event.data["call_id"]
        for event in current_events
        if event.event_type == "tool.call.requested"
        and event.data.get("canonical_name") in {"time.now", "time.in_zone"}
        and event.data.get("call_id")
    }
    acquired: list[datetime] = []
    for event in current_events:
        if (
            event.event_type != "tool.call.completed"
            or event.data.get("call_id") not in requested
            or event.data.get("status") != "success"
        ):
            continue
        output = event.data.get("output", {}).get("outputs", {})
        timestamp = output.get("utc")
        if isinstance(timestamp, str):
            acquired.append(_utc_instant(timestamp))
    assert acquired, "no successful current-turn time acquisition"
    assert answer in acquired, "answer differs from the acquired UTC instant"


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
    for marker in _RAW_TOOL_MARKERS:
        assert marker not in visible, marker
    after_final_done = visible[done_matches[-1].end() :]
    for marker in _CRASH_MARKERS:
        assert marker not in after_final_done, marker
    for marker in _INCOMPLETE_TURN_MARKERS:
        assert marker not in after_final_done, marker


def assert_expected_markers(
    transcript: str, prompt: str, markers: tuple[str, ...]
) -> None:
    output = turn_output_text(transcript, prompt).lower()
    done_matches = list(_DONE_RE.finditer(output))
    if done_matches:
        output = output[: done_matches[-1].start()]
    answer_index = output.rfind("●")
    if answer_index >= 0:
        output = output[answer_index + 1 :]
    for failure_marker in _FAILED_FINAL_ANSWER_MARKERS:
        assert failure_marker not in output, failure_marker
    for marker in markers:
        alternatives = tuple(
            part.strip().lower() for part in marker.split("|") if part.strip()
        )
        assert any(alternative in output for alternative in alternatives), marker
