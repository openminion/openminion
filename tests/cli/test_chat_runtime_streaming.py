from __future__ import annotations

from openminion.cli.chat.runtime import build_chat_progress_callback


class _FakeDisplay:
    def __init__(self) -> None:
        self.notes: list[str] = []
        self.updates: list[object] = []

    def emit_note(self, text: str) -> None:
        self.notes.append(text)

    def update(self, payload: object) -> None:
        self.updates.append(payload)


def test_build_chat_progress_callback_routes_tool_events_to_notes() -> None:
    display = _FakeDisplay()
    callback = build_chat_progress_callback(phase_display=display)  # type: ignore[arg-type]

    callback({"kind": "tool_started", "tool_name": "web.search", "args": {"q": "x"}})
    callback({"trace_id": "trace-1", "status_key": "working", "label": "Working..."})

    assert len(display.notes) == 1
    note = display.notes[0]
    assert "web.search" in note
    assert note.startswith("⏳")  # running state
    assert len(display.updates) == 1


def test_build_chat_progress_callback_routes_budget_events_to_notes() -> None:
    display = _FakeDisplay()
    callback = build_chat_progress_callback(phase_display=display)  # type: ignore[arg-type]

    callback({"kind": "budget_event", "event_type": "budget.extended", "cap": 8})

    assert display.notes == ["Budget event: budget.extended"]
    assert display.updates == []
