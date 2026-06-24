from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

import pytest

from openminion.modules.registry.models import AgentDescriptor, AgentStatus
from openminion.modules.registry.storage import build_registry_store
from openminion.modules.registry.storage.store import (
    PostgresRegistryStore,
    SQLiteRegistryStore,
)
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


def _descriptor(agent_id: str) -> AgentDescriptor:
    return AgentDescriptor.model_validate(
        {
            "agent_id": agent_id,
            "display_name": agent_id,
            "version": "0.0.1",
            "tags": ["validator"],
            "capabilities": [
                {
                    "name": "validate",
                    "methods": ["validate.factcheck"],
                    "quality_tier": "high",
                    "cost_tier": "standard",
                }
            ],
            "endpoints": [
                {
                    "endpoint_id": "default",
                    "transport": "inproc",
                    "address": f"entrypoint:{agent_id}:handle",
                    "priority": 0,
                    "enabled": True,
                }
            ],
            "auth": {"mode": "none"},
        }
    )


@pytest.fixture(params=_backend_params())
def registry_store_case(request: pytest.FixtureRequest, tmp_path: Path):
    backend = str(request.param)
    with ExitStack() as stack:
        if backend == "sqlite":
            store = SQLiteRegistryStore(tmp_path / "registry.db", wal=False)
        else:
            record_store, _schema_name = stack.enter_context(
                open_postgres_record_store("mpt1_registry")
            )
            store = PostgresRegistryStore(record_store=record_store)
        stack.callback(store.close)
        yield backend, store


def test_registry_store_round_trip(registry_store_case) -> None:
    _backend, store = registry_store_case
    descriptor = _descriptor("validator-1")
    store.upsert_agent(descriptor, "manifest")

    record = store.get_agent_record("validator-1")
    assert record is not None
    assert record.source == "manifest"
    assert [item.agent_id for item in store.list_agent_records()] == ["validator-1"]
    assert store.find_agent_ids_by_method("validate.factcheck") == ["validator-1"]
    assert store.get_method_rows("validate.factcheck")[0].quality_tier == "high"

    status = AgentStatus(agent_id="validator-1", state="healthy")
    store.upsert_status("validator-1", status)
    assert store.get_status("validator-1") is not None
    assert store.list_status({"state": "healthy"})[0].agent_id == "validator-1"

    store.delete_agent("validator-1")
    assert store.get_agent("validator-1") is None
    assert store.find_agent_ids_by_method("validate.factcheck") == []


def test_build_registry_store_returns_sqlite_store(tmp_path: Path) -> None:
    store = build_registry_store(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "registry.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        database_path=tmp_path / "registry.db",
    )
    try:
        assert isinstance(store, SQLiteRegistryStore)
    finally:
        store.close()


@pytest.mark.postgres
def test_build_registry_store_returns_postgres_store(tmp_path: Path) -> None:
    with open_postgres_record_store("mpt1_registry_factory") as (
        _record_store,
        schema_name,
    ):
        store = build_registry_store(
            config=build_postgres_storage_config(
                tmp_path=tmp_path,
                schema_name=schema_name,
                sqlite_name="registry.db",
            ),
            database_path=tmp_path / "registry.db",
        )
        try:
            assert isinstance(store, PostgresRegistryStore)
        finally:
            store.close()
