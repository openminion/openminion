from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from openminion.modules.context.compress.compaction import CompactionService
from openminion.modules.context.compress.schemas import CheckpointFailedPayload
from openminion.modules.context.compress.strategies import DeltaEvent
from openminion.modules.context.compress.events import emit_compress_operation
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_compaction_service_emits_summary_create_refresh_skip_and_error(
    tmp_path: Path,
) -> None:
    service = TelemetryService(str(tmp_path / ".openminion" / "telemetry.db"))
    ctl = TelemetryCtl(service)
    compaction = CompactionService(telemetryctl=ctl)
    compaction.set_telemetry_context(session_id="sess-compress", turn_id="turn-1")
    compaction.update(
        "sess-compress",
        [
            DeltaEvent(
                event_id="evt-1",
                event_type="turn.completed",
                text="Summarize the deploy plan and rollback checks.",
            )
        ],
    )

    assert compaction.maybe_checkpoint("sess-compress", reason="initial") is not None
    compaction.update(
        "sess-compress",
        [
            DeltaEvent(
                event_id="evt-2",
                event_type="turn.completed",
                text="Add the rollback owner handoff notes to refresh coverage.",
            )
        ],
    )
    assert compaction.maybe_checkpoint("sess-compress", reason="refresh") is not None

    empty = CompactionService(telemetryctl=ctl)
    empty.set_telemetry_context(session_id="sess-compress", turn_id="turn-1")
    assert empty.maybe_checkpoint("sess-compress", reason="skip") is None

    failing = CompactionService(telemetryctl=ctl)
    failing.set_telemetry_context(session_id="sess-compress", turn_id="turn-1")
    failing.update(
        "sess-compress",
        [
            DeltaEvent(
                event_id="evt-2",
                event_type="turn.completed",
                text="This checkpoint should force an error path.",
            )
        ],
    )
    failing._composer.compose = lambda **_: CheckpointFailedPayload(  # type: ignore[method-assign]
        failure_id="cp-failed",
        session_id="sess-compress",
        reason="error",
        error_code="forced_failure",
        created_at="2026-03-28T00:00:00+00:00",
        from_event_id=None,
        until_event_id="evt-2",
        details={"message": "forced failure"},
    )
    assert failing.maybe_checkpoint("sess-compress", reason="error") is None

    summary = _run(service.get_module_summary("sess-compress"))
    stats = summary["context.compress"]
    assert stats["operation_counts"]["summary_create"] == 1
    assert stats["operation_counts"]["summary_refresh"] == 1
    assert stats["operation_counts"]["summary_skip"] == 1
    assert stats["operation_counts"]["summary_error"] == 1
    assert stats["custom_counter_sums"]["covered_events"] >= 2.0
    assert stats["custom_counter_sums"]["covered_tokens"] >= 0.0
    _run(service.close())


def test_compress_helper_rejects_unknown_operation_and_absent_adapter() -> None:
    assert (
        emit_compress_operation(
            telemetryctl=None,
            session_id="sess-compress-invalid",
            turn_id="turn-1",
            operation="",
        )
        is False
    )
