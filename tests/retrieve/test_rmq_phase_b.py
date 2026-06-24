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
                # `recency_half_life_hours` dropped;
                # phase-b test does not depend on recency.
                "feedback_decay_halflife_days": 60,
                "decay_min_feedback_score": 0.0,
            },
        },
    }


def _ingest_single_unit(ctl: RetrieveCtl, *, created_at: str | None = None) -> str:
    ctl.ingest_source(
        source_type="doc",
        source_ref="doc://phase-b/decay",
        text="phase-b decay validation text",
        scope="project",
        tags=["phase-b"],
        title="phase-b decay",
        created_at=created_at,
    )
    hits = ctl.retrieve(
        query="phase-b decay validation text",
        purpose="act",
        scope={"session_id": "s-b", "agent_id": "a-b"},
        k=2,
        strategy="auto",
        filters={},
    )
    return str(hits[0]["meta"]["unit_id"])


def test_apply_feedback_decay_decays_stale_and_preserves_recent(tmp_path: Path) -> None:
    ctl = RetrieveCtl(_retrieve_config(tmp_path))
    try:
        unit_id = _ingest_single_unit(ctl)
        now_iso = datetime.now(timezone.utc).isoformat()
        stale_iso = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()

        ctl.set_feedback_scores({unit_id: 0.8})
        ctl.store.execute(
            "UPDATE retrievectl_units SET last_hit_at = ? WHERE unit_id = ?",
            (stale_iso, unit_id),
        )
        ctl.store.commit()
        updated = ctl.store.apply_feedback_decay(
            halflife_days=60,
            min_feedback_score=0.0,
        )
        assert updated >= 1
        stale_score = ctl.feedback_state([unit_id])[unit_id]["feedback_score"]
        assert abs(stale_score - 0.4) < 0.08

        ctl.set_feedback_scores({unit_id: 0.8})
        ctl.store.execute(
            "UPDATE retrievectl_units SET last_hit_at = ? WHERE unit_id = ?",
            (now_iso, unit_id),
        )
        ctl.store.commit()
        ctl.store.apply_feedback_decay(halflife_days=60, min_feedback_score=0.0)
        recent_score = ctl.feedback_state([unit_id])[unit_id]["feedback_score"]
        assert abs(recent_score - 0.8) < 1e-3

        ctl.set_feedback_scores({unit_id: 0.1})
        ctl.store.execute(
            "UPDATE retrievectl_units SET last_hit_at = ? WHERE unit_id = ?",
            (stale_iso, unit_id),
        )
        ctl.store.commit()
        ctl.store.apply_feedback_decay(halflife_days=60, min_feedback_score=0.3)
        floored_score = ctl.feedback_state([unit_id])[unit_id]["feedback_score"]
        assert floored_score >= 0.3
    finally:
        ctl.close()


def test_apply_decay_delegates_and_handles_errors(tmp_path: Path) -> None:
    ctl = RetrieveCtl(_retrieve_config(tmp_path))
    try:
        calls: list[tuple[int, float]] = []

        def _ok_apply(*, halflife_days: int, min_feedback_score: float) -> int:
            calls.append((halflife_days, min_feedback_score))
            return 7

        ctl.store.apply_feedback_decay = _ok_apply  # type: ignore[method-assign]
        assert ctl.apply_decay() == 7
        assert calls

        def _boom_apply(*, halflife_days: int, min_feedback_score: float) -> int:
            raise RuntimeError("boom")

        ctl.store.apply_feedback_decay = _boom_apply  # type: ignore[method-assign]
        assert ctl.apply_decay() == 0
    finally:
        ctl.close()
