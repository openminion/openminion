from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl


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
                "decay_halflife_days": 30,
                "recency_weight": 0.3,
                "k_conversational": 3,
                "k_knowledge": 3,
            },
        },
    }


def test_phase_a_defaults_present(tmp_path: Path) -> None:
    ctl = RetrieveCtl(_retrieve_config(tmp_path))
    try:
        defaults = ctl.config.defaults
        assert defaults.decay_halflife_days == 30
        assert defaults.recency_weight == 0.3
        assert defaults.k_conversational == 3
        assert defaults.k_knowledge == 3
    finally:
        ctl.close()


def test_retrieve_items_include_feedback_meta_fields(tmp_path: Path) -> None:
    ctl = RetrieveCtl(_retrieve_config(tmp_path))
    try:
        created_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        ingest = ctl.ingest_source(
            source_type="doc",
            source_ref="doc://phase-a/meta-fields",
            text="phase-a meta fields are returned for ranking",
            scope="project",
            tags=["phase-a", "meta"],
            title="phase-a meta",
            created_at=created_at,
        )
        assert ingest.unit_count >= 1

        hits = ctl.retrieve(
            query="phase-a meta fields",
            purpose="act",
            scope={"session_id": "s-a", "agent_id": "a-a"},
            k=4,
            strategy="auto",
            filters={"scope_keys": []},
        )
        assert hits
        meta = hits[0].get("meta", {})
        assert isinstance(meta.get("hit_count"), int)
        assert "last_hit_at" in meta
        assert isinstance(meta.get("feedback_score"), float)
    finally:
        ctl.close()


def test_feedback_state_roundtrip_reflected_in_retrieve_meta(tmp_path: Path) -> None:
    ctl = RetrieveCtl(_retrieve_config(tmp_path))
    try:
        ctl.ingest_source(
            source_type="doc",
            source_ref="doc://phase-a/feedback",
            text="phase-a feedback roundtrip check",
            scope="project",
            tags=["phase-a", "feedback"],
            title="phase-a feedback",
        )
        initial = ctl.retrieve(
            query="feedback roundtrip check",
            purpose="act",
            scope={"session_id": "s-a", "agent_id": "a-a"},
            k=3,
            strategy="auto",
            filters={},
        )
        unit_id = str(initial[0]["meta"]["unit_id"])
        ctl.record_hits([unit_id])
        ctl.set_feedback_scores({unit_id: 0.25})

        refreshed = ctl.retrieve(
            query="feedback roundtrip check",
            purpose="act",
            scope={"session_id": "s-a", "agent_id": "a-a"},
            k=3,
            strategy="auto",
            filters={},
        )
        refreshed_item = next(
            (
                item
                for item in refreshed
                if str(item.get("meta", {}).get("unit_id", "")) == unit_id
            ),
            None,
        )
        assert refreshed_item is not None
        refreshed_meta = refreshed_item["meta"]
        assert refreshed_meta["hit_count"] >= 1
        assert refreshed_meta["feedback_score"] >= 0.25
    finally:
        ctl.close()
