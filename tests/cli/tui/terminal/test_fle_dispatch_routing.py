from __future__ import annotations

import asyncio
import io
from typing import Any
from unittest.mock import MagicMock, patch

from rich.console import Console

from openminion.cli.tui.terminal.shell import _run_agent_turn
from openminion.cli.tui.terminal.status_line import TerminalStatusLine
from openminion.cli.tui.terminal.transcript import TerminalTranscript


# ── Stub streaming runtimes ──────────────────────────────────────


class _ScriptedRuntime:
    def __init__(
        self,
        *,
        pre_chunk_events: list[dict[str, Any]] | None = None,
        chunks: list[str] | None = None,
        post_chunk_events: list[dict[str, Any]] | None = None,
    ) -> None:
        self._pre = list(pre_chunk_events or [])
        self._chunks = list(chunks or [])
        self._post = list(post_chunk_events or [])

    async def send_message(self, text, *, progress_callback=None, **kwargs):
        del text, kwargs
        # Pre-chunk events: tool lifecycle typically fires here,
        # before the model produces text.
        for evt in self._pre:
            if progress_callback:
                progress_callback(evt)
            await asyncio.sleep(0)
        for chunk in self._chunks:
            yield chunk
            await asyncio.sleep(0)
        for evt in self._post:
            if progress_callback:
                progress_callback(evt)
            await asyncio.sleep(0)


def _make_transcript() -> tuple[TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    return TerminalTranscript(Console(file=buf, force_terminal=False, width=160)), buf


def test_tool_started_payload_routes_to_transcript_handler() -> None:
    transcript, _ = _make_transcript()
    spy_started = MagicMock(wraps=transcript.handle_tool_started)
    transcript.handle_tool_started = spy_started  # type: ignore[method-assign]

    runtime = _ScriptedRuntime(
        pre_chunk_events=[
            {
                "kind": "tool_started",
                "call_id": "c1",
                "tool_name": "Bash",
                "args": {"cmd": "ls"},
            }
        ],
        chunks=["hi"],
    )
    asyncio.run(
        _run_agent_turn(
            text="x",
            runtime=runtime,
            transcript=transcript,
            status_line=None,
        )
    )
    assert spy_started.call_count == 1
    assert spy_started.call_args.args[0]["call_id"] == "c1"


def test_tool_started_payload_does_not_reach_phase_display() -> None:
    transcript, _ = _make_transcript()
    fake_phase = MagicMock()
    fake_phase.__enter__ = MagicMock(return_value=fake_phase)
    fake_phase.__exit__ = MagicMock(return_value=None)
    fake_phase.callback = MagicMock()
    fake_phase.clear = MagicMock()

    runtime = _ScriptedRuntime(
        pre_chunk_events=[
            {
                "kind": "tool_started",
                "call_id": "c1",
                "tool_name": "Bash",
                "args": {"cmd": "ls"},
            }
        ],
        chunks=["hi"],
    )
    with patch(
        "openminion.cli.tui.terminal.shell.PhaseStatusDisplay",
        return_value=fake_phase,
    ):
        asyncio.run(
            _run_agent_turn(
                text="x",
                runtime=runtime,
                transcript=transcript,
                status_line=None,
            )
        )
    for call in fake_phase.callback.call_args_list:
        payload = call.args[0] if call.args else {}
        assert payload.get("kind") != "tool_started"


def test_tool_completed_payload_routes_to_transcript_handler() -> None:
    transcript, _ = _make_transcript()
    spy_completed = MagicMock(wraps=transcript.handle_tool_completed)
    transcript.handle_tool_completed = spy_completed  # type: ignore[method-assign]

    runtime = _ScriptedRuntime(
        pre_chunk_events=[
            {
                "kind": "tool_started",
                "call_id": "c1",
                "tool_name": "Bash",
                "args": {"cmd": "ls"},
            },
            {
                "kind": "tool_completed",
                "call_id": "c1",
                "tool_name": "Bash",
                "args": {"cmd": "ls"},
                "content": "ok",
                "exit_code": 0,
            },
        ],
        chunks=["done"],
    )
    asyncio.run(
        _run_agent_turn(
            text="x",
            runtime=runtime,
            transcript=transcript,
            status_line=None,
        )
    )
    assert spy_completed.call_count == 1
    assert spy_completed.call_args.args[0]["call_id"] == "c1"


def test_tool_completed_payload_does_not_reach_phase_display() -> None:
    transcript, _ = _make_transcript()
    fake_phase = MagicMock()
    fake_phase.__enter__ = MagicMock(return_value=fake_phase)
    fake_phase.__exit__ = MagicMock(return_value=None)
    fake_phase.callback = MagicMock()
    fake_phase.clear = MagicMock()

    runtime = _ScriptedRuntime(
        pre_chunk_events=[
            {
                "kind": "tool_completed",
                "call_id": "c1",
                "tool_name": "Bash",
                "args": {"cmd": "ls"},
                "content": "ok",
            }
        ],
        chunks=["done"],
    )
    with patch(
        "openminion.cli.tui.terminal.shell.PhaseStatusDisplay",
        return_value=fake_phase,
    ):
        asyncio.run(
            _run_agent_turn(
                text="x",
                runtime=runtime,
                transcript=transcript,
                status_line=None,
            )
        )
    for call in fake_phase.callback.call_args_list:
        payload = call.args[0] if call.args else {}
        assert payload.get("kind") != "tool_completed"


def test_phase_payload_routes_to_phase_display() -> None:
    transcript, _ = _make_transcript()
    fake_phase = MagicMock()
    fake_phase.__enter__ = MagicMock(return_value=fake_phase)
    fake_phase.__exit__ = MagicMock(return_value=None)
    fake_phase.callback = MagicMock()
    fake_phase.clear = MagicMock()

    runtime = _ScriptedRuntime(
        pre_chunk_events=[{"kind": "phase", "label": "thinking"}],
        chunks=["hi"],
    )
    with patch(
        "openminion.cli.tui.terminal.shell.PhaseStatusDisplay",
        return_value=fake_phase,
    ):
        asyncio.run(
            _run_agent_turn(
                text="x",
                runtime=runtime,
                transcript=transcript,
                status_line=None,
            )
        )
    kinds_seen = [
        (call.args[0] if call.args else {}).get("kind")
        for call in fake_phase.callback.call_args_list
    ]
    assert "phase" in kinds_seen


def test_thinking_payload_routes_to_phase_display() -> None:
    transcript, _ = _make_transcript()
    fake_phase = MagicMock()
    fake_phase.__enter__ = MagicMock(return_value=fake_phase)
    fake_phase.__exit__ = MagicMock(return_value=None)
    fake_phase.callback = MagicMock()
    fake_phase.clear = MagicMock()

    runtime = _ScriptedRuntime(
        pre_chunk_events=[{"kind": "thinking", "label": "reasoning"}],
        chunks=["hi"],
    )
    with patch(
        "openminion.cli.tui.terminal.shell.PhaseStatusDisplay",
        return_value=fake_phase,
    ):
        asyncio.run(
            _run_agent_turn(
                text="x",
                runtime=runtime,
                transcript=transcript,
                status_line=None,
            )
        )
    kinds_seen = [
        (call.args[0] if call.args else {}).get("kind")
        for call in fake_phase.callback.call_args_list
    ]
    assert "thinking" in kinds_seen


def test_payload_without_kind_routes_to_phase_display() -> None:
    transcript, _ = _make_transcript()
    fake_phase = MagicMock()
    fake_phase.__enter__ = MagicMock(return_value=fake_phase)
    fake_phase.__exit__ = MagicMock(return_value=None)
    fake_phase.callback = MagicMock()
    fake_phase.clear = MagicMock()

    runtime = _ScriptedRuntime(
        pre_chunk_events=[{"label": "something"}],
        chunks=["hi"],
    )
    with patch(
        "openminion.cli.tui.terminal.shell.PhaseStatusDisplay",
        return_value=fake_phase,
    ):
        asyncio.run(
            _run_agent_turn(
                text="x",
                runtime=runtime,
                transcript=transcript,
                status_line=None,
            )
        )
    # Was invoked at least once (the no-kind payload routed here).
    assert fake_phase.callback.call_count >= 1


def test_empty_payload_does_not_crash() -> None:
    transcript, _ = _make_transcript()
    runtime = _ScriptedRuntime(
        pre_chunk_events=[{}],
        chunks=["hi"],
    )
    # No assertion — just verify it runs to completion.
    asyncio.run(
        _run_agent_turn(
            text="x",
            runtime=runtime,
            transcript=transcript,
            status_line=None,
        )
    )


# ── (4) Turn handle open before first chunk ───────────────────────


def test_handle_open_when_tool_started_fires_before_any_chunk() -> None:
    transcript, _ = _make_transcript()

    # Capture transcript._messages length right when tool_started fires.
    snapshot: dict[str, int] = {}
    original_handler = transcript.handle_tool_started

    def _capturing_handler(payload):
        snapshot["msg_count_at_tool_start"] = len(transcript._messages)
        return original_handler(payload)

    transcript.handle_tool_started = _capturing_handler  # type: ignore[method-assign]

    runtime = _ScriptedRuntime(
        pre_chunk_events=[
            {
                "kind": "tool_started",
                "call_id": "c1",
                "tool_name": "Bash",
                "args": {"cmd": "ls"},
            }
        ],
        chunks=["hi"],
    )
    asyncio.run(
        _run_agent_turn(
            text="x",
            runtime=runtime,
            transcript=transcript,
            status_line=None,
        )
    )
    # `begin_turn` appended the assistant placeholder before the
    # tool_started fired → msg_count_at_tool_start >= 1.
    assert snapshot.get("msg_count_at_tool_start", 0) >= 1


def test_call_id_recorded_in_dedup_set_after_tool_started() -> None:
    transcript, _ = _make_transcript()
    runtime = _ScriptedRuntime(
        pre_chunk_events=[
            {
                "kind": "tool_started",
                "call_id": "abc-123",
                "tool_name": "Bash",
                "args": {"cmd": "ls"},
            }
        ],
        chunks=["hi"],
    )
    asyncio.run(
        _run_agent_turn(
            text="x",
            runtime=runtime,
            transcript=transcript,
            status_line=None,
        )
    )
    assert "abc-123" in transcript._live_narrated_call_ids


# ── (5) FTR-02 footer stays idle across tool events ───────────────


def test_footer_stays_idle_across_tool_events() -> None:
    transcript, _ = _make_transcript()
    status_line = TerminalStatusLine()
    status_line.set_state(agent="alpha", model="openai/test", cwd="/tmp")

    state_history: list[str] = []
    original_set_state = status_line.set_state

    def _spy_set_state(**segments):
        if "state" in segments and segments["state"] is not None:
            state_history.append(str(segments["state"]))
        return original_set_state(**segments)

    status_line.set_state = _spy_set_state  # type: ignore[method-assign]

    runtime = _ScriptedRuntime(
        pre_chunk_events=[
            {
                "kind": "tool_started",
                "call_id": "c1",
                "tool_name": "Bash",
                "args": {},
            },
            {
                "kind": "tool_completed",
                "call_id": "c1",
                "tool_name": "Bash",
                "args": {},
                "content": "ok",
            },
        ],
        chunks=["done"],
    )
    asyncio.run(
        _run_agent_turn(
            text="x",
            runtime=runtime,
            transcript=transcript,
            status_line=status_line,
        )
    )
    assert "responding" not in state_history, (
        f"footer must not flip to responding during tool events; "
        f"history: {state_history}"
    )
    assert status_line.state == "idle"


# ── (6) Bounded-fallback / zero-chunk safety ──────────────────────


def test_zero_chunk_turn_completes_without_dangling_handle() -> None:
    transcript, _ = _make_transcript()
    runtime = _ScriptedRuntime(chunks=[])
    # Should not raise.
    asyncio.run(
        _run_agent_turn(
            text="x",
            runtime=runtime,
            transcript=transcript,
            status_line=None,
        )
    )


def test_runtime_exception_still_completes_handle() -> None:

    class _RaisingRuntime:
        async def send_message(self, text, *, progress_callback=None, **kwargs):
            del text, kwargs, progress_callback
            raise RuntimeError("boom")
            yield ""  # pragma: no cover  — make this a generator

    transcript, _ = _make_transcript()
    asyncio.run(
        _run_agent_turn(
            text="x",
            runtime=_RaisingRuntime(),
            transcript=transcript,
            status_line=None,
        )
    )
    # An ERROR message landed in the transcript.
    from openminion.cli.tui.presentation.models import MessageKind

    error_msgs = [m for m in transcript._messages if m.kind == MessageKind.ERROR]
    assert len(error_msgs) == 1
    assert "boom" in error_msgs[0].body


# ── (7) Mixed event sequence (regression composite) ───────────────


def test_mixed_event_sequence_routes_each_payload_correctly() -> None:
    transcript, _ = _make_transcript()
    fake_phase = MagicMock()
    fake_phase.__enter__ = MagicMock(return_value=fake_phase)
    fake_phase.__exit__ = MagicMock(return_value=None)
    fake_phase.callback = MagicMock()
    fake_phase.clear = MagicMock()

    spy_started = MagicMock(wraps=transcript.handle_tool_started)
    spy_completed = MagicMock(wraps=transcript.handle_tool_completed)
    transcript.handle_tool_started = spy_started  # type: ignore[method-assign]
    transcript.handle_tool_completed = spy_completed  # type: ignore[method-assign]

    runtime = _ScriptedRuntime(
        pre_chunk_events=[
            {"kind": "phase", "label": "thinking"},
            {
                "kind": "tool_started",
                "call_id": "c1",
                "tool_name": "Bash",
                "args": {},
            },
            {
                "kind": "tool_completed",
                "call_id": "c1",
                "tool_name": "Bash",
                "args": {},
                "content": "out",
            },
            {"kind": "phase", "label": "respond"},
        ],
        chunks=["final"],
    )
    with patch(
        "openminion.cli.tui.terminal.shell.PhaseStatusDisplay",
        return_value=fake_phase,
    ):
        asyncio.run(
            _run_agent_turn(
                text="x",
                runtime=runtime,
                transcript=transcript,
                status_line=None,
            )
        )

    # Tool handlers each fired once.
    assert spy_started.call_count == 1
    assert spy_completed.call_count == 1
    # PhaseStatusDisplay saw the two phase events (and never any
    # tool_started / tool_completed payloads).
    kinds = [
        (call.args[0] if call.args else {}).get("kind")
        for call in fake_phase.callback.call_args_list
    ]
    assert kinds.count("phase") >= 2
    assert "tool_started" not in kinds
    assert "tool_completed" not in kinds
