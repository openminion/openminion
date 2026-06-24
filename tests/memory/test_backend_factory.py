from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
    SearchQueryOptions,
)
from openminion.modules.memory.storage.factory import resolve_memory_backend
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore

pytestmark = pytest.mark.postgres


def test_resolve_memory_backend_common_backends(tmp_path: Path) -> None:
    for config, db_name, backend, store_type, supports_transactions in (
        (None, "memory.db", "sqlite", SQLiteMemoryStore, True),
        (
            {"store": {"backend": "mock"}},
            "ignored.db",
            "mock",
            InMemoryMemoryStore,
            False,
        ),
    ):
        resolved = resolve_memory_backend(config=config, db_path=tmp_path / db_name)
        assert resolved.backend == backend
        assert isinstance(resolved.store, store_type)
        assert resolved.capabilities.supports_transactions is supports_transactions


@pytest.mark.postgres
def test_resolve_memory_backend_supports_postgres_backend() -> None:
    import os
    import sqlalchemy as sa
    import uuid

    from tests.storage.postgres_test_utils import schema_url

    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")

    schema_name = f"memory_factory_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    try:
        resolved = resolve_memory_backend(
            config={
                "store": {
                    "backend": "postgres",
                    "postgres": {"url": schema_url(postgres_url, schema_name)},
                }
            },
            db_path=Path("/unused/memory.db"),
        )
        assert resolved.backend == "postgres"
        assert isinstance(resolved.store, PostgresMemoryStore)
        assert resolved.capabilities.supports_transactions is True
        resolved.store.close()
    finally:
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_mock_backend_passes_memory_service_contract_basics(tmp_path: Path) -> None:
    resolved = resolve_memory_backend(
        config={"store": {"backend": "mock"}},
        db_path=tmp_path / "ignored.db",
    )
    service = MemoryService(store=resolved.store)

    record_id = service.write_record(
        scope="session:s1",
        record_type="fact",
        title="orion",
        content={"text": "orion memory"},
    )
    got = service.get(record_id)
    assert got.id == record_id

    listed = service.list(ListQueryOptions(scopes=["session:s1"], limit=5))
    assert any(item.id == record_id for item in listed)

    hits = service.search(
        SearchQueryOptions(query="orion", scopes=["session:s1"], limit=5)
    )
    assert any(item.id == record_id for item in hits)

    candidate_id = service.stage_candidate(
        scope="session:s1",
        record_type="fact",
        title="candidate fact",
        content={"text": "candidate orion"},
    )
    candidates = service.candidate_list(CandidateListOptions(session_id="s1", limit=10))
    assert any(item.candidate_id == candidate_id for item in candidates)

    service.candidate_update(candidate_id, {"status": "approved"})
    promoted = service.promote_candidate(candidate_id, "agent:main")
    assert isinstance(promoted, MemoryRecord)
    assert promoted.scope == "agent:main"


def test_sqlite_and_mock_backends_share_basic_search_semantics(tmp_path: Path) -> None:
    services = [
        MemoryService(
            store=resolve_memory_backend(
                config={"store": {"backend": backend}},
                db_path=tmp_path / db_name,
            ).store
        )
        for backend, db_name in (
            ("sqlite", "memory.db"),
            ("mock", "ignored.db"),
        )
    ]

    for service in services:
        service.write_record(
            scope="session:sx",
            record_type="fact",
            title="alpha record",
            content={"text": "alpha memory"},
        )
        service.write_record(
            scope="session:sx",
            record_type="fact",
            title="beta record",
            content={"text": "beta memory"},
        )

    query = SearchQueryOptions(query="alpha", scopes=["session:sx"], limit=5)
    for hits in (service.search(query) for service in services):
        assert len(hits) >= 1
