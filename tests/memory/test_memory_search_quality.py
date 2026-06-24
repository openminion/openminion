from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest
import sqlalchemy as sa

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.storage.base import SearchQueryOptions
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from tests.storage.postgres_test_utils import schema_url

pytestmark = pytest.mark.postgres


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _build_corpus():
    now = _now()
    return [
        MemoryRecord(
            id=record_id,
            scope="session:s1",
            type="fact",
            title=title,
            content=content,
            tags=tags,
            entities=entities,
            created_at=now,
            updated_at=now,
        )
        for record_id, title, content, tags, entities in (
            (
                "r1",
                "deploy checklist",
                "deploy checklist for aurora rollback",
                ["deploy", "aurora"],
                ["Aurora"],
            ),
            (
                "r2",
                "cluster phrase alpha",
                "duplicate cluster phrase alpha alpha variation",
                ["cluster"],
                ["Alpha"],
            ),
            (
                "r3",
                "mdc generalization",
                "mdc generalization e2e is active",
                ["mdc"],
                ["Generalization"],
            ),
            (
                "r4",
                "kyoto weather",
                "weather in Kyoto is partly cloudy",
                ["weather", "kyoto"],
                ["Kyoto"],
            ),
        )
    ]


def _populate(store) -> None:
    for record in _build_corpus():
        store.put(record)


@pytest.fixture
def sqlite_store(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _populate(store)
    return store


@pytest.fixture
def postgres_store():
    postgres_url = str(os.environ.get("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")
    schema_name = f"mpt3_search_{datetime.datetime.now(datetime.timezone.utc).strftime('%H%M%S%f')}"
    admin_engine = sa.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    engine = sa.create_engine(schema_url(postgres_url, schema_name), future=True)
    try:
        store = PostgresMemoryStore(
            engine,
            database_path=Path.cwd() / ".openminion-memory-postgres-search-test",
        )
        _populate(store)
        yield store
    finally:
        engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.postgres
@pytest.mark.parametrize(
    "query,required_ids",
    [
        ("aurora", {"r1"}),
        ("cluster phrase alpha", {"r2"}),
        ('"cluster phrase alpha"', {"r2"}),
        ("mdc*", {"r3"}),
        ("remember this fact: mdc-generalization-e2e is active", {"r3"}),
        ("no-such-memory", set()),
    ],
)
def test_search_quality_sets_match_between_sqlite_and_postgres(
    sqlite_store,
    postgres_store,
    query: str,
    required_ids: set[str],
) -> None:
    sqlite_hits = sqlite_store.search(
        SearchQueryOptions(query=query, scopes=["session:s1"], limit=10)
    )
    postgres_hits = postgres_store.search(
        SearchQueryOptions(query=query, scopes=["session:s1"], limit=10)
    )

    sqlite_ids = {item.id for item in sqlite_hits}
    postgres_ids = {item.id for item in postgres_hits}
    assert sqlite_ids == postgres_ids
    assert required_ids.issubset(sqlite_ids)
