from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

import pytest

from openminion.modules.storage.engine import StorageEngineConfig
from openminion.modules.telemetry.storage import build_telemetry_store
from openminion.modules.telemetry.storage.store import (
    PostgresTelemetryStore,
    SQLiteTelemetryStore,
)
from tests.storage.postgres_test_utils import (
    build_postgres_storage_config,
    open_postgres_record_store,
)


def _backend_params():
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", marks=pytest.mark.postgres, id="postgres"),
    ]


@pytest.fixture(params=_backend_params())
def telemetry_store_case(request: pytest.FixtureRequest, tmp_path: Path):
    backend = str(request.param)
    with ExitStack() as stack:
        if backend == "sqlite":
            store = SQLiteTelemetryStore(tmp_path / "telemetry.db")
        else:
            record_store, _schema_name = stack.enter_context(
                open_postgres_record_store("mpt1_telemetry")
            )
            store = PostgresTelemetryStore(record_store=record_store)
        stack.callback(store.close)
        yield backend, store


def test_telemetry_store_round_trip(telemetry_store_case) -> None:
    _backend, store = telemetry_store_case
    store.insert_event(
        session_id="sess-1",
        turn_id="turn-1",
        event_type="tool.completed",
        timestamp=1.25,
        data={"tool": "time"},
    )
    store.insert_event(
        session_id="sess-1",
        turn_id="turn-2",
        event_type="llm_call",
        timestamp=2.5,
        data={"model": "haiku"},
    )

    rows = store.fetch_session_events("sess-1")
    assert rows == [
        ("turn-1", "tool.completed", 1.25, '{"tool": "time"}'),
        ("turn-2", "llm_call", 2.5, '{"model": "haiku"}'),
    ]
    assert store.fetch_session_events("missing") == []


def test_build_telemetry_store_returns_sqlite_store(tmp_path: Path) -> None:
    store = build_telemetry_store(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "telemetry.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        database_path=tmp_path / "telemetry.db",
    )
    try:
        assert isinstance(store, SQLiteTelemetryStore)
    finally:
        store.close()


@pytest.mark.postgres
def test_build_telemetry_store_returns_postgres_store(tmp_path: Path) -> None:
    with open_postgres_record_store("mpt1_telemetry_factory") as (
        _record_store,
        schema_name,
    ):
        store = build_telemetry_store(
            config=build_postgres_storage_config(
                tmp_path=tmp_path,
                schema_name=schema_name,
                sqlite_name="telemetry.db",
            ),
            database_path=tmp_path / "telemetry.db",
        )
        try:
            assert isinstance(store, PostgresTelemetryStore)
        finally:
            store.close()
