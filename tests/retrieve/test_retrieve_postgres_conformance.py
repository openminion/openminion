from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

import pytest

from openminion.modules.retrieve.storage import build_retrieve_store
from openminion.modules.retrieve.storage.store import (
    PostgresRetrieveStore,
    SQLiteRetrieveStore,
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


@pytest.fixture(params=_backend_params())
def retrieve_store_case(request: pytest.FixtureRequest, tmp_path: Path):
    backend = str(request.param)
    with ExitStack() as stack:
        if backend == "sqlite":
            store = SQLiteRetrieveStore(tmp_path / "retrieve.db")
            stack.callback(store.close)
        else:
            record_store, _schema_name = stack.enter_context(
                open_postgres_record_store("mpt2_retrieve")
            )
            store = PostgresRetrieveStore(record_store=record_store)
        store.ensure_schema()
        yield backend, store


def test_retrieve_store_round_trip(retrieve_store_case) -> None:
    _backend, store = retrieve_store_case

    docs_columns = store._table_columns("retrievectl_docs")  # noqa: SLF001
    assert "scope_key" in docs_columns
    units_columns = store._table_columns("retrievectl_units")  # noqa: SLF001
    assert "feedback_score" in units_columns

    store.execute(
        """
        INSERT INTO retrievectl_docs(
            doc_id, source_type, source_ref, scope, tags_json, created_at, updated_at, title, corpus_id, scope_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "doc-1",
            "mem",
            "mem:1",
            "project",
            '["weather"]',
            "2026-04-01T00:00:00+00:00",
            "2026-04-01T00:00:00+00:00",
            "Doc 1",
            None,
            "project:main",
        ),
    )
    store.execute(
        """
        INSERT INTO retrievectl_units(
            unit_id, doc_id, unit_kind, level, node_id, text_ref, context_text_ref,
            fts_text, created_at, token_count, group_id, offsets_json, hit_count, last_hit_at, feedback_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "unit-1",
            "doc-1",
            "chunk",
            "none",
            None,
            "blob://unit-1",
            None,
            "weather in sf",
            "2026-04-01T00:00:00+00:00",
            3,
            None,
            "{}",
            0,
            None,
            0.0,
        ),
    )
    if getattr(store, "fts_enabled", False):
        store.execute(
            """
            INSERT INTO retrievectl_units_fts(unit_id, title, fts_text, tags)
            VALUES (?, ?, ?, ?)
            """,
            ("unit-1", "Doc 1", "weather in sf", "weather"),
        )
    else:
        store.execute(
            """
            INSERT INTO retrievectl_units_fts(unit_id, title, fts_text, tags)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(unit_id) DO UPDATE SET
                title=excluded.title,
                fts_text=excluded.fts_text,
                tags=excluded.tags
            """,
            ("unit-1", "Doc 1", "weather in sf", "weather"),
        )
    store.commit()

    row = store.fetchone(
        "SELECT unit_id, feedback_score FROM retrievectl_units WHERE unit_id = ?",
        ("unit-1",),
    )
    assert row is not None
    assert row["unit_id"] == "unit-1"

    rows = store.fetchall(
        "SELECT unit_id FROM retrievectl_units WHERE doc_id = ?",
        ("doc-1",),
    )
    assert rows[0]["unit_id"] == "unit-1"

    assert store.get_feedback_state(["unit-1"]) == {
        "unit-1": {
            "hit_count": 0,
            "last_hit_at": None,
            "feedback_score": 0.0,
        }
    }
    assert store.record_hits(["unit-1"], observed_at="2026-04-01T01:00:00+00:00") == 1
    assert store.set_feedback_scores({"unit-1": 0.8}) == 1
    assert store.apply_feedback_decay(halflife_days=30, min_feedback_score=0.1) >= 0


def test_build_retrieve_store_returns_sqlite_store(tmp_path: Path) -> None:
    store = build_retrieve_store(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "retrieve.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        database_path=tmp_path / "retrieve.db",
    )
    try:
        assert isinstance(store, SQLiteRetrieveStore)
    finally:
        store.close()


@pytest.mark.postgres
def test_build_retrieve_store_returns_postgres_store(tmp_path: Path) -> None:
    with open_postgres_record_store("mpt2_retrieve_factory") as (
        _record_store,
        schema_name,
    ):
        store = build_retrieve_store(
            config=build_postgres_storage_config(
                tmp_path=tmp_path,
                schema_name=schema_name,
                sqlite_name="retrieve.db",
            ),
            database_path=tmp_path / "retrieve.db",
        )
        try:
            assert isinstance(store, PostgresRetrieveStore)
        finally:
            store.close()
