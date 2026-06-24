from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
from openminion.modules.retrieve.schemas import RetrievalFilters


def _config(tmp_path: Path) -> dict:
    return {
        "version": 1,
        "retrievectl": {
            "storage": {
                "sqlite_path": str(tmp_path / "retrievectl.db"),
                "blob_root": str(tmp_path / "blob"),
                "wal_mode": False,
            },
            "defaults": {
                "strategy": "contextual",
                "contextual_enabled": True,
                "embeddings_enabled": False,
                "lexical_candidate_count": 25,
                "snippet_tokens": 120,
                "chunk_target_tokens": 30,
                "chunk_min_tokens": 15,
                "chunk_max_tokens": 35,
                "doc_group_target_tokens": 40,
                "doc_group_min_tokens": 25,
                "doc_group_max_tokens": 60,
                "raptor_internal_k": 2,
                "raptor_leaf_k": 4,
                # `recency_half_life_hours` dropped;
                # scope-keys test does not depend on recency.
            },
        },
    }


def _service(tmp_path: Path) -> RetrieveCtl:
    return RetrieveCtl(_config(tmp_path))


def test_retrieval_filters_accept_scope_keys() -> None:
    filters = RetrievalFilters.model_validate(
        {
            "tags": ["ops"],
            "scope_keys": ["session:s-1", "agent:a-1"],
        }
    )
    assert filters.scope_keys == ["session:s-1", "agent:a-1"]


def test_retrievectl_retrieve_accepts_scope_keys_without_regression(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    try:
        service.ingest_source(
            source_type="mem",
            source_ref="mem://scope-keys",
            text="scope-key compatibility retrieval sample",
            scope="agent",
            tags=["compat"],
            title="Scope key compatibility",
            unit_kind="chunk",
        )
        service.store.execute(
            "UPDATE retrievectl_docs SET scope_key = ? WHERE source_ref = ?",
            ("agent:demo", "mem://scope-keys"),
        )
        service.store.commit()
        rows = service.retrieve(
            query="scope key compatibility",
            purpose="act",
            scope={"agent": True},
            k=3,
            strategy="contextual",
            filters={"scope_keys": ["agent:demo", "session:s-123"]},
        )
        assert rows
    finally:
        service.close()


def test_retrievectl_forwards_scope_keys_to_candidate_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    captured_scope_keys: list[str] = []

    def _capture_generate_candidates(*, query, scope, filters, limit):  # type: ignore[no-untyped-def]
        del query, scope, limit
        captured_scope_keys.extend(filters.scope_keys)
        return []

    monkeypatch.setattr(service, "_generate_candidates", _capture_generate_candidates)
    try:
        rows = service.retrieve(
            query="noop",
            purpose="act",
            scope={"agent": True},
            k=3,
            strategy="contextual",
            filters={"scope_keys": ["session:s-222", "agent:a-222"]},
        )
        assert rows == []
        assert captured_scope_keys == ["session:s-222", "agent:a-222"]
    finally:
        service.close()


def test_scope_keys_filter_constrains_search_results(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        service.ingest_source(
            source_type="mem",
            source_ref="mem://agent-scope",
            text="scope filter shared token AGENT_ONLY",
            scope="agent",
            tags=["scope"],
            title="Agent scope row",
            unit_kind="chunk",
        )
        service.ingest_source(
            source_type="mem",
            source_ref="mem://session-scope",
            text="scope filter shared token SESSION_ONLY",
            scope="session",
            tags=["scope"],
            title="Session scope row",
            unit_kind="chunk",
        )
        service.store.execute(
            "UPDATE retrievectl_docs SET scope_key = ? WHERE source_ref = ?",
            ("agent:agent-123", "mem://agent-scope"),
        )
        service.store.execute(
            "UPDATE retrievectl_docs SET scope_key = ? WHERE source_ref = ?",
            ("session:session-123", "mem://session-scope"),
        )
        service.store.commit()

        session_rows = service.retrieve(
            query="scope filter shared token",
            purpose="act",
            scope={"session": True, "agent": True},
            k=10,
            strategy="contextual",
            filters={"scope_keys": ["session:session-123"]},
        )
        assert session_rows
        assert all(
            "SESSION_ONLY" in str(item.get("text_snippet", "")) for item in session_rows
        )
        assert all(
            "AGENT_ONLY" not in str(item.get("text_snippet", ""))
            for item in session_rows
        )
    finally:
        service.close()


def test_empty_scope_keys_preserves_open_query_behavior(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        service.ingest_source(
            source_type="mem",
            source_ref="mem://open-agent",
            text="open query shared token OPEN_AGENT",
            scope="agent",
            tags=["scope"],
            title="Open query agent row",
            unit_kind="chunk",
        )
        service.ingest_source(
            source_type="mem",
            source_ref="mem://open-session",
            text="open query shared token OPEN_SESSION",
            scope="session",
            tags=["scope"],
            title="Open query session row",
            unit_kind="chunk",
        )
        service.store.execute(
            "UPDATE retrievectl_docs SET scope_key = ? WHERE source_ref = ?",
            ("agent:agent-open", "mem://open-agent"),
        )
        service.store.execute(
            "UPDATE retrievectl_docs SET scope_key = ? WHERE source_ref = ?",
            ("session:session-open", "mem://open-session"),
        )
        service.store.commit()

        rows = service.retrieve(
            query="open query shared token",
            purpose="act",
            scope={"session": True, "agent": True},
            k=10,
            strategy="contextual",
            filters={"scope_keys": []},
        )
        snippets = [str(item.get("text_snippet", "")) for item in rows]
        assert any("OPEN_AGENT" in snippet for snippet in snippets)
        assert any("OPEN_SESSION" in snippet for snippet in snippets)
    finally:
        service.close()
