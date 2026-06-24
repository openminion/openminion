from __future__ import annotations

import sqlite3
from pathlib import Path

from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database
from openminion.services.context.session import SessionContextService


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
                "chunk_target_tokens": 16,
                "chunk_min_tokens": 4,
                "chunk_max_tokens": 20,
                "doc_group_target_tokens": 16,
                "doc_group_min_tokens": 4,
                "doc_group_max_tokens": 24,
                "raptor_internal_k": 2,
                "raptor_leaf_k": 4,
            },
        },
    }


def test_rcb_session_episode_ingestion_and_retrieve(tmp_path: Path) -> None:
    runtime_db = tmp_path / "state" / "openminion.db"
    migrate_database(runtime_db)
    session_conn = connect_database(runtime_db)
    sessions = SessionStore(session_conn)
    retrieve_ctl = RetrieveCtl(_retrieve_config(tmp_path))

    try:
        session = sessions.resolve_session(
            agent_id="rcb-se-agent", channel="console", target="chat"
        )
        for i in range(25):
            role = "inbound" if i % 2 == 0 else "outbound"
            if i == 3:
                body = "user mention: launch codename is zephyr"
            else:
                body = f"turn {i} general project discussion entry"
            sessions.append_message(session_id=session.id, role=role, body=body)

        service = SessionContextService(
            sessions,
            keep_recent_messages=2,
            max_compact_per_turn=100,
            retrieve_ctl=retrieve_ctl,
        )
        compact_result = service.compact_session(session_id=session.id)
        assert compact_result.compacted_count >= 20

        with sqlite3.connect(str(tmp_path / "retrievectl.db")) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM retrievectl_units u
                JOIN retrievectl_docs d ON d.doc_id = u.doc_id
                WHERE d.source_type = 'episode' AND d.scope = ?
                """,
                ("session",),
            ).fetchone()
            source_ref_row = conn.execute(
                """
                SELECT source_ref
                FROM retrievectl_docs
                WHERE source_type = 'episode'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        assert row is not None
        # Session compaction ingests adjacent user->assistant messages as one turn-pair unit.
        min_expected_units = (compact_result.compacted_count + 1) // 2
        assert int(row[0]) >= min_expected_units
        assert int(row[0]) <= compact_result.compacted_count
        assert source_ref_row is not None
        assert str(source_ref_row[0]).startswith(f"session:{session.id}#rowid:")

        hits = retrieve_ctl.retrieve(
            query="launch codename zephyr",
            purpose="act",
            scope={"session_id": session.id, "agent_id": "rcb-se-agent"},
            k=4,
            strategy="auto",
            filters=None,
        )
        assert hits, "expected at least one retrieved episode hit"
        assert any(str(item.get("ref_type", "")).lower() == "episode" for item in hits)
        assert any("zephyr" in str(item.get("text", "")).lower() for item in hits)
    finally:
        retrieve_ctl.close()
        session_conn.close()
