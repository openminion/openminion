from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event

from openminion.modules.telemetry.export.queueing import NoncriticalExportQueue
from openminion.modules.telemetry.schemas import (
    TelemetryEvent,
    normalize_telemetry_event,
)
from openminion.modules.telemetry.service import TelemetryService
from openminion.modules.telemetry.trace.layout import (
    resolve_trace_root,
    write_protected_trace_file,
)


class _DeletionExporter:
    def __init__(self, pending: int) -> None:
        self.pending = pending

    def export(self, event: TelemetryEvent) -> bool:
        return True

    def delete_pending_invocation(self, invocation_id: str) -> int:
        assert invocation_id == "invocation-1"
        return self.pending

    def close(self) -> None:
        return


def test_database_artifact_and_pending_export_deletion_is_auditable(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    service = TelemetryService(home_root=tmp_path)
    service._otel_exporter = _DeletionExporter(pending=2)
    asyncio.run(
        service.record_event(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                event_type="agent.execution.completed",
                invocation_id="invocation-1",
            )
        )
    )
    trace_path = resolve_trace_root(home_root=tmp_path) / "trace.json"
    write_protected_trace_file(
        trace_path,
        '{"trace": {"invocation_id": "invocation-1"}}',
    )

    result = service.delete_invocation("invocation-1")

    assert result.database_rows_deleted == 1
    assert result.artifacts_deleted == 1
    assert result.pending_exports_deleted == 2
    assert result.external_collector_status == "accepted_data_not_retractable"
    assert service._store.fetch_invocation_events("invocation-1") == []
    assert not trace_path.exists()
    audit = service._store.fetch_session_events("telemetry")[-1]
    assert audit.event_type == "telemetry.retention.deleted"
    assert audit.data["external_collector_status"] == ("accepted_data_not_retractable")
    service.close_sync()


def test_pending_queue_deletion_removes_only_matching_invocation() -> None:
    first_started = Event()
    release_first = Event()

    def export_now(event: TelemetryEvent) -> bool:
        if event.invocation_id == "busy":
            first_started.set()
            release_first.wait(timeout=2)
        return True

    queue = NoncriticalExportQueue(
        capacity=3,
        flush_timeout_seconds=2,
        export_now=export_now,
    )
    busy = normalize_telemetry_event(
        TelemetryEvent(
            session_id="session",
            turn_id="turn",
            event_type="agent.execution.completed",
            invocation_id="busy",
            data={"criticality": "trace"},
        )
    )
    target = normalize_telemetry_event(
        TelemetryEvent(
            session_id="session",
            turn_id="turn",
            event_type="agent.execution.completed",
            invocation_id="target",
            data={"criticality": "trace"},
        )
    )
    assert queue.enqueue(busy)
    assert first_started.wait(timeout=2)
    assert queue.enqueue(target)
    assert queue.delete_pending_invocation("target") == 1
    assert queue.stats()["queue_depth"] == 0
    release_first.set()
    queue.close()


def test_trace_and_database_permissions_are_restrictive(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    service = TelemetryService(home_root=tmp_path)
    trace_path = resolve_trace_root(home_root=tmp_path) / "protected.json"
    write_protected_trace_file(trace_path, "{}")

    db_path = Path(service._db_path)
    assert db_path.stat().st_mode & 0o777 == 0o600
    assert db_path.parent.stat().st_mode & 0o777 == 0o700
    assert trace_path.stat().st_mode & 0o777 == 0o600
    assert trace_path.parent.stat().st_mode & 0o777 == 0o700
    service.close_sync()
