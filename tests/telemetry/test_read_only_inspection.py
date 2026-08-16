from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from openminion.modules.telemetry.inspection import open_telemetry_inspection
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService


def _event(index: int, *, timestamp: float | None = None) -> TelemetryEvent:
    return TelemetryEvent(
        session_id="session-1",
        turn_id="turn-1",
        event_type="agent.invocation.started" if index == 0 else "tick",
        event_id=f"event-{index}",
        timestamp=float(index if timestamp is None else timestamp),
        invocation_id="invocation-1",
        data={"index": index},
    )


def _snapshot(path: Path) -> dict[str, tuple[int, int, int, str]]:
    rows = {}
    for candidate in (
        path,
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
        Path(f"{path}-journal"),
    ):
        if not candidate.exists():
            continue
        stat = candidate.stat()
        rows[candidate.name] = (
            stat.st_mode,
            stat.st_size,
            stat.st_mtime_ns,
            hashlib.sha256(candidate.read_bytes()).hexdigest(),
        )
    return rows


def test_missing_database_returns_empty_without_creating_parent(tmp_path: Path) -> None:
    parent = tmp_path / ".openminion" / "existing"
    parent.mkdir(parents=True)
    path = parent / "telemetry.db"

    with open_telemetry_inspection(db_path=path) as service:
        assert service is None

    assert path.parent == parent
    assert not path.exists()


def test_missing_parent_fails_without_creation(tmp_path: Path) -> None:
    path = tmp_path / ".openminion" / "missing" / "telemetry.db"

    with pytest.raises(PermissionError):
        with open_telemetry_inspection(db_path=path):
            pass

    assert not path.parent.exists()


def test_read_only_query_preserves_database_and_sidecars(tmp_path: Path) -> None:
    path = tmp_path / ".openminion" / "telemetry.db"
    writer = TelemetryService(db_path=str(path))
    writer.record_event_sync(_event(0))
    writer.record_event_sync(_event(1))
    before = _snapshot(path)
    try:
        with open_telemetry_inspection(db_path=path) as reader:
            assert reader is not None
            high_water = reader._store.event_high_water(invocation_id="invocation-1")
            page = reader._store.fetch_event_page(
                high_water=high_water,
                invocation_id="invocation-1",
                limit=10,
            )
            assert [row.event.event_id for row in page] == ["event-1", "event-0"]
            with pytest.raises(sqlite3.OperationalError):
                reader._store.insert_event(_event(2))
        assert _snapshot(path) == before
    finally:
        writer.close_sync()


def test_partial_wal_sidecars_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / ".openminion" / "telemetry.db"
    service = TelemetryService(db_path=str(path))
    service.close_sync()
    Path(f"{path}-wal").write_bytes(b"partial")

    with pytest.raises(RuntimeError, match="partial SQLite WAL sidecars"):
        with open_telemetry_inspection(db_path=path):
            pass


def test_unreadable_wal_sidecar_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / ".openminion" / "telemetry.db"
    service = TelemetryService(db_path=str(path))
    service.close_sync()
    wal = Path(f"{path}-wal")
    shm = Path(f"{path}-shm")
    wal.write_bytes(b"wal")
    shm.write_bytes(b"shm")
    real_access = os.access
    monkeypatch.setattr(
        "openminion.modules.storage.record_store.os.access",
        lambda candidate, mode: (
            False if Path(candidate) == wal else real_access(candidate, mode)
        ),
    )

    with pytest.raises(PermissionError):
        with open_telemetry_inspection(db_path=path):
            pass


def test_rollback_journal_database_opens_read_only(tmp_path: Path) -> None:
    path = tmp_path / ".openminion" / "telemetry.db"
    from openminion.modules.telemetry.storage.store import SQLiteTelemetryStore

    store = SQLiteTelemetryStore(path, wal=False)
    store.insert_event(_event(0))
    store.close()

    with open_telemetry_inspection(db_path=path) as reader:
        assert reader is not None
        assert reader._store.event_high_water() == 1


def test_bounded_pages_hold_the_original_high_water(tmp_path: Path) -> None:
    service = TelemetryService(db_path=str(tmp_path / ".openminion" / "telemetry.db"))
    try:
        for index in range(1002):
            service.record_event_sync(_event(index, timestamp=10.0))
        high_water = service._store.event_high_water(invocation_id="invocation-1")
        service.record_event_sync(_event(1002, timestamp=11.0))
        first = service._store.fetch_event_page(
            high_water=high_water,
            invocation_id="invocation-1",
            limit=1000,
        )
        second = service._store.fetch_event_page(
            high_water=high_water,
            invocation_id="invocation-1",
            before_timestamp=first[-1].event.timestamp,
            before_id=first[-1].row_id,
            limit=1000,
        )
    finally:
        service.close_sync()

    assert len(first) == 1000
    assert [row.event.event_id for row in second] == ["event-1", "event-0"]
    assert "event-1002" not in {row.event.event_id for row in first + second}


def test_exact_session_turn_owner_lookup_is_bounded(tmp_path: Path) -> None:
    service = TelemetryService(db_path=str(tmp_path / ".openminion" / "telemetry.db"))
    try:
        service.record_event_sync(_event(0))
        service.record_event_sync(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                event_type="tick",
                event_id="other-owner",
                timestamp=1.0,
                invocation_id="invocation-2",
            )
        )
        high_water = service._store.event_high_water()
        service.record_event_sync(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                event_type="tick",
                event_id="later-owner",
                timestamp=2.0,
                invocation_id="invocation-3",
            )
        )
        assert service._store.find_turn_invocation_ids(
            session_id="session-1",
            turn_id="turn-1",
            high_water=high_water,
        ) == ["invocation-2", "invocation-1"]
        with pytest.raises(ValueError, match="between 1 and 1000"):
            service._store.fetch_event_page(high_water=1, limit=1001)
    finally:
        service.close_sync()


def test_sqlite_legacy_text_timestamp_is_read_as_numeric(tmp_path: Path) -> None:
    path = tmp_path / ".openminion" / "telemetry.db"
    service = TelemetryService(db_path=str(path))
    service.record_event_sync(_event(0))
    service.close_sync()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE events SET timestamp = 'malformed' WHERE id = 1")
        connection.commit()

    with open_telemetry_inspection(db_path=path) as reader:
        assert reader is not None
        page = reader._store.fetch_event_page(high_water=1, limit=1)

    assert page[0].event.timestamp == 0.0
    assert page[0].timestamp_valid is False
