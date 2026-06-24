from __future__ import annotations

from pathlib import Path

from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _retrieve_config(tmp_path: Path) -> dict:
    return {
        "version": 1,
        "retrievectl": {
            "storage": {
                "sqlite_path": str(tmp_path / "retrievectl.db"),
                "blob_root": str(tmp_path / "retrieve-blobs"),
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


def test_rcb_dual_query_integration(tmp_path: Path) -> None:
    memory_store = SQLiteMemoryStore(tmp_path / "memory.db")
    memory_service = MemoryService(store=memory_store)
    retrieve_ctl = RetrieveCtl(_retrieve_config(tmp_path))
    adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="rcb-qry-agent",
        retrieve_ctl=retrieve_ctl,
    )

    try:
        retrieve_ctl.ingest_source(
            source_type="doc",
            source_ref="doc://rcb-qry/project-stack",
            text="The project uses Python 3.12 with FastAPI for the service layer.",
            scope="project",
            tags=["project", "stack"],
            title="Project technology stack",
            unit_kind="chunk",
        )
        adapter.record_turn(
            session_id="s-dual",
            run_id="r1",
            request_id="req1",
            channel="chat",
            target="user",
            user_message="fact: local memory says deployment uses uvicorn",
            assistant_message="",
        )

        content, _meta = adapter.build_retrieval_context_with_metadata(
            session_id="s-dual",
            user_message="what uses uvicorn and python 3.12?",
        )

        assert "local memory says deployment uses uvicorn" in content
        assert "Python 3.12" in content
        assert "FastAPI" in content
    finally:
        retrieve_ctl.close()
