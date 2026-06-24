from __future__ import annotations

from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _retrieve_config(tmp_path) -> dict:
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
                "recency_weight": 0.3,
                "k_conversational": 3,
                "k_knowledge": 2,
            },
        },
    }


def _extract_bullets(content: str) -> list[str]:
    return [
        line.strip()[2:].strip()
        for line in content.splitlines()
        if line.strip().startswith("• ")
    ]


def _ingest_todo_fact(retrieve_ctl: RetrieveCtl, *, agent_id: str) -> None:
    source_ref = f"mem://phase-c/{agent_id}"
    retrieve_ctl.ingest_source(
        source_type="mem",
        source_ref=source_ref,
        text="todo buy milk and eggs before standup",
        scope="project",
        tags=["phase-c", "todo"],
        title="todo shopping note",
    )
    retrieve_ctl.store.execute(
        "UPDATE retrievectl_docs SET scope_key = ? WHERE source_ref = ?",
        (f"agent:{agent_id}", source_ref),
    )
    retrieve_ctl.store.commit()


def test_phase_c_direct_retrieval_keeps_exact_match_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)
    memory_service = MemoryService(store=SQLiteMemoryStore(tmp_path / "memory.db"))
    retrieve_ctl = RetrieveCtl(_retrieve_config(tmp_path))
    adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="rmq-c-direct",
        retrieve_ctl=retrieve_ctl,
    )
    try:
        _ingest_todo_fact(retrieve_ctl, agent_id="rmq-c-direct")
        content, _meta = adapter.build_retrieval_context_with_metadata(
            session_id="s-c-direct",
            user_message="what todo should I do next?",
        )
        bullets = _extract_bullets(content)
        assert any("todo buy milk and eggs before standup" in line for line in bullets)
    finally:
        retrieve_ctl.close()


def test_phase_c_direct_retrieval_does_not_bridge_phrase_gap(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)
    memory_service = MemoryService(store=SQLiteMemoryStore(tmp_path / "memory.db"))
    retrieve_ctl = RetrieveCtl(_retrieve_config(tmp_path))
    adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="rmq-c-direct",
        retrieve_ctl=retrieve_ctl,
    )
    try:
        _ingest_todo_fact(retrieve_ctl, agent_id="rmq-c-direct")
        content, _meta = adapter.build_retrieval_context_with_metadata(
            session_id="s-c-direct",
            user_message="what task should I do next?",
        )
        bullets = _extract_bullets(content)
        assert bullets == []
        assert "todo buy milk and eggs before standup" not in content
    finally:
        retrieve_ctl.close()
