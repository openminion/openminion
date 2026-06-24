from __future__ import annotations

import sqlite3
from pathlib import Path

from openminion.modules.retrieve.storage.migrations import list_migrations
from openminion.modules.retrieve.storage.store import SQLiteRetrieveStore


def _create_legacy_schema(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE retrievectl_docs(
                doc_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                scope TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                corpus_id TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE retrievectl_units(
                unit_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                unit_kind TEXT NOT NULL,
                level TEXT NULL,
                node_id TEXT NULL,
                text_ref TEXT NOT NULL,
                context_text_ref TEXT NULL,
                fts_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                token_count INTEGER NOT NULL DEFAULT 0,
                group_id TEXT NULL,
                offsets_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(doc_id) REFERENCES retrievectl_docs(doc_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE retrievectl_units_fts
            USING fts5(unit_id UNINDEXED, fts_text, tags)
            """
        )
        conn.commit()


def test_retrieve_migrations_include_phase0_schema_revision() -> None:
    assert "v2_scope_key_feedback_schema" in list_migrations()


def test_phase0_schema_fields_exist_with_defaults_for_new_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "retrieve.db"
    store = SQLiteRetrieveStore(db_path)
    try:
        store.ensure_schema()

        docs_columns = store._table_columns("retrievectl_docs")  # noqa: SLF001
        assert "scope_key" in docs_columns

        units_columns = store._table_columns("retrievectl_units")  # noqa: SLF001
        assert "hit_count" in units_columns
        assert "last_hit_at" in units_columns
        assert "feedback_score" in units_columns

        store.execute(
            """
            INSERT INTO retrievectl_docs(
                doc_id, source_type, source_ref, scope, tags_json, created_at, updated_at, title, corpus_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "doc-new",
                "mem",
                "mem:new",
                "project",
                "[]",
                "2026-03-20T00:00:00+00:00",
                "2026-03-20T00:00:00+00:00",
                "new",
                None,
            ),
        )
        store.execute(
            """
            INSERT INTO retrievectl_units(
                unit_id, doc_id, unit_kind, level, node_id, text_ref, context_text_ref, fts_text, created_at, token_count, group_id, offsets_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "unit-new",
                "doc-new",
                "chunk",
                "none",
                None,
                "blob://new",
                None,
                "new content",
                "2026-03-20T00:00:00+00:00",
                3,
                None,
                "{}",
            ),
        )
        store.commit()

        doc_row = store.fetchone(
            "SELECT scope_key FROM retrievectl_docs WHERE doc_id = ?",
            ("doc-new",),
        )
        assert doc_row is not None
        assert str(doc_row["scope_key"]) == "global:legacy"

        unit_row = store.fetchone(
            """
            SELECT hit_count, last_hit_at, feedback_score
            FROM retrievectl_units
            WHERE unit_id = ?
            """,
            ("unit-new",),
        )
        assert unit_row is not None
        assert int(unit_row["hit_count"]) == 0
        assert unit_row["last_hit_at"] is None
        assert float(unit_row["feedback_score"]) == 0.0
    finally:
        store.close()


def test_legacy_rows_are_backfilled_with_safe_phase0_defaults(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-retrieve.db"
    _create_legacy_schema(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO retrievectl_docs(
                doc_id, source_type, source_ref, scope, tags_json, created_at, updated_at, title, corpus_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "doc-legacy",
                "episode",
                "session:legacy-row",
                "agent",
                "[]",
                "2026-03-19T00:00:00+00:00",
                "2026-03-19T00:00:00+00:00",
                "legacy",
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO retrievectl_units(
                unit_id, doc_id, unit_kind, level, node_id, text_ref, context_text_ref, fts_text, created_at, token_count, group_id, offsets_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "unit-legacy",
                "doc-legacy",
                "chunk",
                "none",
                None,
                "blob://legacy",
                None,
                "legacy content",
                "2026-03-19T00:00:00+00:00",
                2,
                None,
                "{}",
            ),
        )
        conn.execute(
            """
            INSERT INTO retrievectl_units_fts(unit_id, fts_text, tags)
            VALUES (?, ?, ?)
            """,
            ("unit-legacy", "legacy content", "legacy"),
        )
        conn.commit()

    store = SQLiteRetrieveStore(db_path)
    try:
        store.ensure_schema()

        doc_row = store.fetchone(
            "SELECT scope, scope_key FROM retrievectl_docs WHERE doc_id = ?",
            ("doc-legacy",),
        )
        assert doc_row is not None
        assert str(doc_row["scope"]) == "agent"
        assert str(doc_row["scope_key"]) == "agent:legacy"

        unit_row = store.fetchone(
            """
            SELECT hit_count, last_hit_at, feedback_score
            FROM retrievectl_units
            WHERE unit_id = ?
            """,
            ("unit-legacy",),
        )
        assert unit_row is not None
        assert int(unit_row["hit_count"]) == 0
        assert unit_row["last_hit_at"] is None
        assert float(unit_row["feedback_score"]) == 0.0
        fts_columns = store._table_columns("retrievectl_units_fts")  # noqa: SLF001
        assert "title" in fts_columns
        fts_row = store.fetchone(
            """
            SELECT title, fts_text, tags
            FROM retrievectl_units_fts
            WHERE unit_id = ?
            """,
            ("unit-legacy",),
        )
        assert fts_row is not None
        assert str(fts_row["title"]) == "legacy"
        assert str(fts_row["fts_text"]) == "legacy content"
    finally:
        store.close()
