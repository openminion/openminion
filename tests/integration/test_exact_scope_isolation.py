from __future__ import annotations

from pathlib import Path

from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl


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
            },
        },
    }


def test_exact_scope_filters_isolate_session_results(tmp_path: Path) -> None:
    service = RetrieveCtl(_config(tmp_path))
    try:
        service.ingest_source(
            source_type="mem",
            source_ref="mem://session-doc",
            text="integration scope token SESSION_SCOPE_ONLY",
            scope="session",
            tags=["scope", "integration"],
            title="Session scoped doc",
            unit_kind="chunk",
        )
        service.ingest_source(
            source_type="mem",
            source_ref="mem://agent-doc",
            text="integration scope token AGENT_SCOPE_ONLY",
            scope="agent",
            tags=["scope", "integration"],
            title="Agent scoped doc",
            unit_kind="chunk",
        )
        service.store.execute(
            "UPDATE retrievectl_docs SET scope_key = ? WHERE source_ref = ?",
            ("session:s-int", "mem://session-doc"),
        )
        service.store.execute(
            "UPDATE retrievectl_docs SET scope_key = ? WHERE source_ref = ?",
            ("agent:a-int", "mem://agent-doc"),
        )
        service.store.commit()

        exact_rows = service.retrieve(
            query="integration scope token",
            purpose="act",
            scope={"session": True, "agent": True},
            k=10,
            strategy="contextual",
            filters={"scope_keys": ["session:s-int"]},
        )
        assert exact_rows
        snippets = [str(item.get("text_snippet", "")) for item in exact_rows]
        assert any("SESSION_SCOPE_ONLY" in snippet for snippet in snippets)
        assert all("AGENT_SCOPE_ONLY" not in snippet for snippet in snippets)

        open_rows = service.retrieve(
            query="integration scope token",
            purpose="act",
            scope={"session": True, "agent": True},
            k=10,
            strategy="contextual",
            filters={"scope_keys": []},
        )
        open_snippets = [str(item.get("text_snippet", "")) for item in open_rows]
        assert any("SESSION_SCOPE_ONLY" in snippet for snippet in open_snippets)
        assert any("AGENT_SCOPE_ONLY" in snippet for snippet in open_snippets)
    finally:
        service.close()
