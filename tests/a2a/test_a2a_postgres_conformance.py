from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

import pytest

from openminion.modules.a2a.models import AgentDescriptor, JobRecord
from openminion.modules.a2a.storage import build_a2a_state_store
from openminion.modules.a2a.storage.store import PostgresStateStore, SQLiteStateStore
from openminion.modules.storage.engine import StorageEngineConfig
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
def a2a_state_store_case(request: pytest.FixtureRequest, tmp_path: Path):
    backend = str(request.param)
    with ExitStack() as stack:
        if backend == "sqlite":
            store = SQLiteStateStore(tmp_path / "a2a.db")
            stack.callback(store.close)
        else:
            record_store, _schema_name = stack.enter_context(
                open_postgres_record_store("mpt2_a2a")
            )
            store = PostgresStateStore(record_store=record_store)
        yield backend, store


def test_a2a_state_store_round_trip(a2a_state_store_case) -> None:
    _backend, store = a2a_state_store_case

    created, record = store.reserve_idempotency("idem-1", "scope-1")
    assert created is True
    assert record is not None

    created_again, existing = store.reserve_idempotency("idem-1", "scope-1")
    assert created_again is False
    assert existing is not None
    assert existing.status == "IN_PROGRESS"

    result = store.set_idempotency_result(
        "idem-1",
        "scope-1",
        "done",
        result_inline={"ok": True},
        task_id="task-1",
    )
    assert result.task_id == "task-1"

    store.create_job(
        JobRecord(
            task_id="task-1",
            trace_id="trace-1",
            idempotency_key="idem-1",
            agent_id="agent-1",
            method="job.start",
            state="running",
            current_step="step-1",
            progress=0.1,
            result_inline=None,
            result_ref=None,
            error=None,
            created_at="2026-04-01T00:00:00+00:00",
            updated_at="2026-04-01T00:00:00+00:00",
            heartbeat_at="2026-04-01T00:00:00+00:00",
        )
    )
    updated_job = store.update_job(
        "task-1",
        {
            "state": "done",
            "progress": 1.0,
            "result_inline": {"ok": True},
        },
    )
    assert updated_job.state == "done"
    assert store.get_job("task-1") is not None
    assert store.list_jobs({"states": ["done"]})[0].task_id == "task-1"

    store.upsert_agent(
        AgentDescriptor(
            agent_id="agent-1",
            capabilities=["echo"],
            endpoint="inproc://agent-1",
            tags=["test"],
            status="ready",
        )
    )
    agents = store.list_agents()
    assert agents[0].agent_id == "agent-1"
    assert agents[0].capabilities == ["echo"]


def test_build_a2a_state_store_returns_sqlite_store(tmp_path: Path) -> None:
    store = build_a2a_state_store(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "a2a.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        database_path=tmp_path / "a2a.db",
    )
    try:
        assert isinstance(store, SQLiteStateStore)
    finally:
        store.close()


@pytest.mark.postgres
def test_build_a2a_state_store_returns_postgres_store(tmp_path: Path) -> None:
    with open_postgres_record_store("mpt2_a2a_factory") as (_record_store, schema_name):
        store = build_a2a_state_store(
            config=build_postgres_storage_config(
                tmp_path=tmp_path,
                schema_name=schema_name,
                sqlite_name="a2a.db",
            ),
            database_path=tmp_path / "a2a.db",
        )
        try:
            assert isinstance(store, PostgresStateStore)
        finally:
            store.close()
