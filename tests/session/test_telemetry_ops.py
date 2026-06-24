from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from openminion.modules.brain.adapters.session import SessctlAdapter
from openminion.modules.session.diagnostics.events import (
    emit_session_operation,
)
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_session_adapter_emits_turn_pack_tool_loop_and_retry(tmp_path: Path) -> None:
    telemetry = TelemetryService(str(tmp_path / ".openminion" / "telemetry.db"))
    ctl = TelemetryCtl(telemetry)
    adapter = SessctlAdapter(tmp_path / "sessions.db", telemetryctl=ctl)
    adapter.set_telemetry_context(session_id="sess-session", turn_id="turn-1")
    adapter.append_turn("sess-session", "user", "hello")
    adapter.append_turn("sess-session", "assistant", "hi")
    adapter.get_slice("sess-session", "act", {"max_turns": 5})
    adapter.append_event(
        "sess-session",
        "tool.completed",
        {"ok": True},
        trace_id="turn-1",
    )
    adapter.emit_canonical_event(
        "sess-session",
        "llm.call.retry",
        payload={"attempt": 2},
        trace_id="turn-1",
    )

    summary = _run(telemetry.get_module_summary("sess-session"))
    stats = summary["openminion-session"]
    assert stats["operation_counts"]["turn_start"] == 1
    assert stats["operation_counts"]["turn_finish"] == 1
    assert stats["operation_counts"]["llm_pack"] >= 1
    assert stats["operation_counts"]["tool_loop"] >= 1
    assert stats["operation_counts"]["retry"] >= 1
    session_summary = _run(telemetry.get_session_summary("sess-session"))
    event_types = [event.event_type for event in session_summary.events]
    assert "turn.user" in event_types
    assert "turn.assistant" in event_types
    assert "tool.completed" in event_types
    assert "llm.call.retry" in event_types
    _run(telemetry.close())


def test_session_helper_rejects_unknown_operation_and_absent_adapter() -> None:
    assert (
        emit_session_operation(
            telemetryctl=None,
            session_id="sess-session-invalid",
            turn_id="turn-1",
            operation="unknown",
        )
        is False
    )
