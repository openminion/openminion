from __future__ import annotations

import sqlite3

import pytest

from openminion.modules.telemetry.schemas import (
    TELEMETRY_EVENT_SCHEMA_V1,
    TELEMETRY_EVENT_SCHEMA_V2,
    TelemetryEvent,
    normalize_telemetry_event,
)
from openminion.modules.telemetry.storage.store import SQLiteTelemetryStore


def _event(**overrides: object) -> TelemetryEvent:
    values = {
        "session_id": "session-1",
        "turn_id": "turn-1",
        "event_type": "agent.execution.started",
        "timestamp": 1.25,
        "data": {},
    }
    values.update(overrides)
    return normalize_telemetry_event(TelemetryEvent(**values))


def test_v1_normalization_creates_v2_identity_without_guessing_invocation() -> None:
    event = normalize_telemetry_event(
        TelemetryEvent(
            session_id="session-1",
            turn_id="turn-1",
            event_type="tick",
            data={"trace_id": "local-correlation", "run_id": "run-1"},
        )
    )

    assert event.schema_version == TELEMETRY_EVENT_SCHEMA_V2
    assert event.event_id
    assert event.trace_key == "local-correlation"
    assert event.invocation_id is None
    assert event.execution_id is None


def test_v2_round_trip_and_indexed_queries(tmp_path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "telemetry.db")
    first = _event(
        invocation_id="invocation-1",
        execution_id="execution-1",
        agent_id="agent-1",
    )
    second = _event(
        turn_id="turn-2",
        timestamp=2.5,
        invocation_id="invocation-1",
        execution_id="execution-2",
        agent_id="agent-1",
    )
    store.insert_event(first)
    store.insert_event(second)

    assert store.fetch_session_events("session-1") == [first, second]
    assert store.fetch_invocation_events("invocation-1") == [first, second]
    assert store.fetch_execution_events("execution-1") == [first]

    with sqlite3.connect(tmp_path / "telemetry.db") as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(events)")}
    assert {
        "idx_events_event_id",
        "idx_events_invocation_time",
        "idx_events_execution_time",
    } <= indexes
    store.close()


def test_duplicate_event_id_is_rejected(tmp_path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "telemetry.db")
    event = _event(event_id="event-1")
    store.insert_event(event)
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_event(event)
    store.close()


def test_legacy_row_remains_readable_without_fabricated_correlation(tmp_path) -> None:
    db_path = tmp_path / "telemetry.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp REAL NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO events(session_id, turn_id, event_type, timestamp, data) "
            "VALUES (?, ?, ?, ?, ?)",
            ("legacy-session", "legacy-turn", "tick", 3.0, "{}"),
        )

    store = SQLiteTelemetryStore(db_path)
    event = store.fetch_session_events("legacy-session")[0]
    assert event.schema_version == TELEMETRY_EVENT_SCHEMA_V1
    assert event.event_id == ""
    assert event.invocation_id is None
    assert event.execution_id is None
    store.close()

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(events)")}
    assert {
        "schema_version",
        "event_id",
        "trace_key",
        "invocation_id",
        "execution_id",
        "agent_id",
        "mode",
    }.issubset(columns)
    assert {
        "idx_events_event_id",
        "idx_events_invocation_time",
        "idx_events_execution_time",
    }.issubset(indexes)
