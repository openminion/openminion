from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from openminion.services.runtime.manager import (
    AgentRuntimeManager,
    TurnRequest,
    TurnResponse,
    TurnTelemetry,
)
from openminion.services.runtime.events import emit_runtime_operation
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_runtime_manager_emits_turn_start_retry_and_finish(tmp_path: Path) -> None:
    telemetry = TelemetryService(str(tmp_path / ".openminion" / "telemetry.db"))
    ctl = TelemetryCtl(telemetry)

    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del req, emit_chunk, cancel_event
        return TurnResponse(
            final_text="ok",
            telemetry=TurnTelemetry(retries=2),
        )

    manager = AgentRuntimeManager(turn_executor=_executor, telemetryctl=ctl)
    manager.start()
    try:
        handle = manager.submit_turn(
            TurnRequest(
                trace_id="trace-runtime",
                agent_id="agent-runtime",
                session_id="sess-runtime",
                input_text="hello",
            )
        )
        result = handle.result(timeout_s=2)
        assert result.final_text == "ok"
    finally:
        manager.shutdown()

    summary = _run(telemetry.get_module_summary("sess-runtime"))
    stats = summary["openminion-runtime"]
    assert stats["operation_counts"]["turn_start"] == 1
    assert stats["operation_counts"]["retry"] == 2
    assert stats["operation_counts"]["turn_finish"] == 1
    _run(telemetry.close())


def test_runtime_helper_rejects_unknown_operation_and_absent_adapter() -> None:
    assert (
        emit_runtime_operation(
            telemetryctl=None,
            session_id="sess-runtime-invalid",
            turn_id="turn-1",
            operation="unknown",
        )
        is False
    )
