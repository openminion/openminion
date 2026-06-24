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


def _service(tmp_path: Path) -> RetrieveCtl:
    return RetrieveCtl(_config(tmp_path))


def test_feedback_helpers_read_and_write_feedback_state(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        service.ingest_source(
            source_type="mem",
            source_ref="mem://feedback-row",
            text="feedback helper state sample text",
            scope="agent",
            tags=["feedback"],
            title="Feedback helper row",
            unit_kind="chunk",
        )
        unit_row = service.store.execute(
            """
            SELECT u.unit_id
            FROM retrievectl_units u
            JOIN retrievectl_docs d ON d.doc_id = u.doc_id
            WHERE d.source_ref = ?
            LIMIT 1
            """,
            ("mem://feedback-row",),
        ).fetchone()
        assert unit_row is not None
        unit_id = str(unit_row["unit_id"])

        initial = service.feedback_state([unit_id])
        assert unit_id in initial
        assert initial[unit_id]["hit_count"] == 0
        assert initial[unit_id]["last_hit_at"] is None
        assert initial[unit_id]["feedback_score"] == 0.0

        hits_updated = service.record_hits(
            [unit_id, unit_id, "missing-unit"],
            observed_at="2026-03-20T20:00:00+00:00",
        )
        assert hits_updated == 1

        after_hits = service.feedback_state([unit_id])
        assert after_hits[unit_id]["hit_count"] == 1
        assert after_hits[unit_id]["last_hit_at"] == "2026-03-20T20:00:00+00:00"
        assert after_hits[unit_id]["feedback_score"] == 0.0

        score_updated = service.set_feedback_scores(
            {
                unit_id: 1.25,
                "missing-unit": 0.5,
            }
        )
        assert score_updated == 1
        after_high_score = service.feedback_state([unit_id])
        assert after_high_score[unit_id]["feedback_score"] == 1.0

        score_updated = service.set_feedback_scores({unit_id: -0.5})
        assert score_updated == 1
        after_low_score = service.feedback_state([unit_id])
        assert after_low_score[unit_id]["feedback_score"] == 0.0
    finally:
        service.close()
