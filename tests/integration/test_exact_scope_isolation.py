from __future__ import annotations

import logging
from pathlib import Path

from openminion.modules.memory.runtime.retrieval_pipeline import RetrievalPipeline
from openminion.modules.retrieve.errors import RetrieveCtlError
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


def test_memory_pipeline_knowledge_scope_includes_only_current_and_global(
    tmp_path: Path,
) -> None:
    service = RetrieveCtl(_config(tmp_path))
    rows = {
        "SAME_SESSION": "session:s-a",
        "SAME_AGENT": "agent:a",
        "SAME_PROJECT": "project:a",
        "EXPLICIT_GLOBAL": "global:legacy",
        "OTHER_AGENT": "agent:b",
        "OTHER_PROJECT": "project:b",
        "AMBIGUOUS_LEGACY": "project:legacy",
    }
    try:
        for label, scope_key in rows.items():
            service.ingest_source(
                source_type="doc",
                source_ref=f"doc://{label.lower()}",
                text=f"knowledge isolation shared token {label}",
                scope=scope_key,
            )
        pipeline = RetrievalPipeline(
            retrieve_ctl=service,
            config=service.config,
            ranking_config=None,
            logger=logging.getLogger("test.exact-scope"),
            agent_id="a",
            retrieval_max_chars=4096,
            trace_fn=None,
            retrieve_error_type=RetrieveCtlError,
        )

        hits, counts = pipeline._retrieve_split(  # noqa: SLF001
            service,
            query="knowledge isolation shared token",
            session_id="s-a",
            agent_id="a",
            project_id="a",
            k_conversational=10,
            k_knowledge=20,
        )

        snippets = {str(item.get("text_snippet", "")) for item in hits}
        assert counts == {"conversational": 0, "knowledge": 4}
        assert all(
            any(label in snippet for snippet in snippets)
            for label in (
                "SAME_SESSION",
                "SAME_AGENT",
                "SAME_PROJECT",
                "EXPLICIT_GLOBAL",
            )
        )
        assert all(
            all(label not in snippet for snippet in snippets)
            for label in ("OTHER_AGENT", "OTHER_PROJECT", "AMBIGUOUS_LEGACY")
        )
    finally:
        service.close()
