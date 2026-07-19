from __future__ import annotations

from typing import Any

from openminion.modules.brain.runner.coordinator import BrainRunner


def _make_minimal_runner() -> BrainRunner:
    runner = object.__new__(BrainRunner)
    runner._progress_callback = None  # type: ignore[attr-defined]
    return runner


def _capture_events() -> tuple[BrainRunner, list[dict[str, Any]]]:
    runner = _make_minimal_runner()
    received: list[dict[str, Any]] = []
    runner._progress_callback = received.append  # type: ignore[attr-defined]
    return runner, received


def test_emit_tool_progress_event_is_noop_when_no_callback() -> None:
    runner = _make_minimal_runner()
    runner._emit_tool_progress_event(
        kind="tool_started",
        tool_name="bash",
        args={"command": "pytest"},
        call_id="call-1",
    )


def test_emit_tool_progress_event_tool_started_dict_shape() -> None:
    runner, received = _capture_events()

    runner._emit_tool_progress_event(
        kind="tool_started",
        tool_name="bash",
        args={"command": "ls"},
        call_id="call-42",
    )

    assert len(received) == 1
    payload = received[0]
    assert payload["kind"] == "tool_started"
    assert payload["tool_name"] == "bash"
    assert payload["args"] == {"command": "ls"}
    assert payload["call_id"] == "call-42"
    assert payload["state"] == "running"
    # Default-safe provenance placeholders match the executor_runtime
    # shape so the gateway streaming extractor and TUI consumers don't
    # break when brain dispatch lacks runtime binding metadata.
    assert payload["model_tool_name"] == ""
    assert payload["runtime_tool_name"] == ""
    assert payload["runtime_binding_id"] == ""
    assert payload["runtime_fallback_used"] is False
    assert payload["runtime_fallback_chain"] == []
    assert payload["runtime_resolution_source"] == ""
    assert payload["fallback_index"] == 0


def test_emit_tool_progress_event_tool_completed_ok() -> None:
    runner, received = _capture_events()

    runner._emit_tool_progress_event(
        kind="tool_completed",
        tool_name="bash",
        args={"command": "ls"},
        call_id="call-42",
        duration_ms=123,
        ok=True,
        content="file1\nfile2\n",
    )

    payload = received[0]
    assert payload["kind"] == "tool_completed"
    assert payload["ok"] is True
    assert payload["state"] == "ok"
    assert payload["duration_ms"] == 123
    assert payload["content"] == "file1\nfile2\n"


def test_emit_tool_progress_event_tool_completed_failed() -> None:
    runner, received = _capture_events()

    runner._emit_tool_progress_event(
        kind="tool_completed",
        tool_name="git",
        args={"command": "status"},
        call_id="call-7",
        duration_ms=50,
        ok=False,
        content="permission denied",
    )

    payload = received[0]
    assert payload["ok"] is False
    assert payload["state"] == "error"
    assert payload["content"] == "permission denied"
    assert payload["duration_ms"] == 50


def test_emit_tool_progress_event_unknown_kind_dropped() -> None:
    runner, received = _capture_events()

    runner._emit_tool_progress_event(kind="bogus", tool_name="x")
    runner._emit_tool_progress_event(kind="", tool_name="x")
    assert received == []


def test_emit_tool_progress_event_callback_exception_is_swallowed() -> None:
    runner = _make_minimal_runner()

    def _crashy(_payload: Any) -> None:
        raise RuntimeError("boom")

    runner._progress_callback = _crashy  # type: ignore[attr-defined]

    # Brain execution must not crash if a consumer's progress callback
    # raises — the bridge swallows downstream exceptions like the
    # executor_runtime emitter does.
    runner._emit_tool_progress_event(kind="tool_started", tool_name="bash")


def test_emit_tool_progress_event_invalid_duration_ms_dropped() -> None:
    runner, received = _capture_events()

    runner._emit_tool_progress_event(
        kind="tool_completed",
        tool_name="bash",
        duration_ms="not-an-int",  # type: ignore[arg-type]
        ok=True,
    )
    payload = received[0]
    assert "duration_ms" not in payload


def test_shared_progress_builder_renders_brain_emitted_payload() -> None:
    from openminion.cli.presentation.tool.progress import (
        build_tool_event_from_progress,
    )

    runner, received = _capture_events()

    runner._emit_tool_progress_event(
        kind="tool_started",
        tool_name="bash",
        args={"command": "ls -la"},
        call_id="c1",
    )
    event = build_tool_event_from_progress(received[0])
    assert event is not None
    assert event.tool_name == "bash"


def test_terminal_consumer_keying_recognizes_brain_emitted_payload() -> None:
    runner, received = _capture_events()

    runner._emit_tool_progress_event(
        kind="tool_started",
        tool_name="git",
        args={"command": "status"},
        call_id="c2",
    )
    payload = received[0]
    kind = str(payload.get("kind", "") or "").strip()
    assert kind == "tool_started"

    runner._emit_tool_progress_event(
        kind="tool_completed",
        tool_name="git",
        args={"command": "status"},
        call_id="c2",
        duration_ms=10,
        ok=True,
        content="On branch main",
    )
    payload = received[1]
    assert str(payload.get("kind", "") or "").strip() == "tool_completed"


def test_focus_rich_consumer_keying_uses_call_id_to_index_widgets() -> None:
    runner, received = _capture_events()

    runner._emit_tool_progress_event(
        kind="tool_started",
        tool_name="bash",
        args={"command": "ls"},
        call_id="stable-id",
    )
    runner._emit_tool_progress_event(
        kind="tool_completed",
        tool_name="bash",
        args={"command": "ls"},
        call_id="stable-id",
        duration_ms=5,
        ok=True,
        content="ok",
    )
    assert received[0]["call_id"] == "stable-id"
    assert received[1]["call_id"] == "stable-id"


def test_dashboard_chat_consumer_kind_prefix_match() -> None:
    runner, received = _capture_events()

    runner._emit_tool_progress_event(kind="tool_started", tool_name="bash")
    runner._emit_tool_progress_event(
        kind="tool_completed",
        tool_name="bash",
        duration_ms=1,
        ok=True,
    )
    assert str(received[0]["kind"]).startswith("tool_")
    assert str(received[1]["kind"]).startswith("tool_")
