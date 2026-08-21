from __future__ import annotations

from pathlib import Path

from openminion.modules.telemetry.service import TelemetryService


def test_debug_query_indexes_are_installed(tmp_path: Path) -> None:
    service = TelemetryService(db_path=str(tmp_path / ".openminion" / "telemetry.db"))
    try:
        rows = service._store._record_store.query_dicts("PRAGMA index_list(events)")
    finally:
        service.close_sync()

    names = {str(row["name"]) for row in rows}
    assert {
        "idx_events_type_time_invocation_id",
        "idx_events_invocation_time_id",
        "idx_events_session_turn_invocation_id",
    }.issubset(names)
