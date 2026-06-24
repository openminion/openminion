from __future__ import annotations

from dataclasses import replace

import pytest

from tests.helpers.memory_e2e_helpers import E2EMemoryHarness
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.runtime.scorer import score_records


def _case_typed_memory_candidate_promotion(tmp_path) -> None:
    # moved to brain AFE; seed promotion-ready candidate directly.
    harness = E2EMemoryHarness(tmp_path, agent_id="matrix-typed")
    harness.seed_candidate(
        session_id="typed",
        content="I prefer dark mode.",
        title="dark mode preference",
    )
    harness.run_turn("typed", "Thanks.", "Done.")
    preferences = harness.query_records(types=["user_preference"], limit=10)
    assert preferences and preferences[0].type == "user_preference"


def _case_ranking_contradiction(tmp_path) -> None:
    harness = E2EMemoryHarness(tmp_path, agent_id="matrix-ranking")
    harness.run_turn("ranking", "remember: Use tabs for indentation.", "Okay.")
    harness.run_turn(
        "ranking", "remember: Actually use spaces for indentation.", "Updated."
    )
    capsule = harness.build_capsule("ranking", "what indentation style?")
    assert "spaces" in capsule.lower()
    assert capsule.lower().index("spaces") < capsule.lower().index("tabs")


def _case_candidate_gc_confidence_decay(tmp_path) -> None:
    harness = E2EMemoryHarness(
        tmp_path,
        agent_id="matrix-gc",
        config_overrides={"candidate_learning": {"candidate_max_age_days": 1}},
    )
    candidate_id = harness.seed_candidate(
        session_id="gc",
        content="I prefer blue themes.",
        title="blue themes preference",
        promotion_ready=False,
    )
    harness.advance_time(2, candidate_ids=[candidate_id])
    harness.run_turn("gc", "Another topic.", "Sure.")
    candidates = harness.query_candidates(session_id="gc", limit=10)
    assert candidates[0].status == "rejected"


def _case_reflection_confidence_decay(tmp_path) -> None:
    harness = E2EMemoryHarness(tmp_path, agent_id="matrix-insight")
    harness.service.upsert_record(
        scope="agent:matrix-insight",
        record_type="meta_insight",
        key="insight:test",
        record_patch={
            "title": "Test insight",
            "content": "A recurring note.",
            "confidence": 0.8,
        },
    )
    insight = harness.query_records(types=["meta_insight"], limit=10)[0]
    harness.advance_time(45, record_ids=[insight.id])
    harness.trigger_lifecycle("insight-gc")
    decayed = harness.service.get(insight.id)
    assert decayed.confidence < 0.8


def _case_preference_boost_cooldown(tmp_path) -> None:
    harness = E2EMemoryHarness(tmp_path, agent_id="matrix-cooldown")
    base = harness.service.upsert_record(
        scope="agent:matrix-cooldown",
        record_type="user_preference",
        key="pref:dark",
        record_patch={
            "title": "Dark mode",
            "content": "I prefer dark mode.",
            "confidence": 0.4,
        },
    )
    for index in range(5):
        harness.seed_summary(
            key=f"summary:{index}",
            summary_text="I prefer dark mode.",
            keywords=["dark-mode"],
        )
    harness.trigger_reflection()
    first = harness.service.get(base.id).confidence
    harness.trigger_reflection()
    second = harness.service.get(base.id).confidence
    assert second == first


def _case_scope_isolation_reflection(tmp_path) -> None:
    shared_store = E2EMemoryHarness(tmp_path, agent_id="matrix-a").store
    agent_a = E2EMemoryHarness(tmp_path, agent_id="matrix-a", store=shared_store)
    agent_b = E2EMemoryHarness(tmp_path, agent_id="matrix-b", store=shared_store)
    for index in range(3):
        agent_a.seed_summary(
            key=f"summary:{index}",
            summary_text="Use ruff rather than flake8.",
            corrections=["Use ruff rather than flake8."],
        )
    written = agent_b.trigger_reflection()
    assert written == 0


def _case_supersession_reasons_keyed_upsert(tmp_path) -> None:
    harness = E2EMemoryHarness(tmp_path, agent_id="matrix-upsert")
    harness.service.upsert_record(
        scope="agent:matrix-upsert",
        record_type="fact",
        key="project:ci",
        record_patch={"title": "CI", "content": "CI uses deploy keys."},
    )
    harness.service.upsert_record(
        scope="agent:matrix-upsert",
        record_type="fact",
        key="project:ci",
        record_patch={"title": "CI", "content": "CI uses OIDC."},
    )
    rows = harness.raw_sql(
        """
        SELECT supersession_reason
        FROM memory_records
        WHERE scope = ? AND key = ?
        ORDER BY created_at ASC
        """,
        ("agent:matrix-upsert", "project:ci"),
    )
    assert any(str(row["supersession_reason"] or "") == "keyed_upsert" for row in rows)


def _case_disuse_decay_capsule_retrieval(tmp_path) -> None:
    harness = E2EMemoryHarness(tmp_path, agent_id="matrix-disuse")
    record = harness.service.upsert_record(
        scope="agent:matrix-disuse",
        record_type="fact",
        key="stale:fact",
        record_patch={
            "title": "Stale fact",
            "content": "This fact should fall below the retrieval threshold.",
            "confidence": 0.61,
        },
    )
    harness.advance_time(45, record_ids=[record.id])
    harness.trigger_lifecycle("disuse-lifecycle")
    capsule = harness.build_capsule("disuse-capsule", "what fact should I know?")
    assert "fall below the retrieval threshold" not in capsule


def _case_meta_insight_type_bonus(tmp_path) -> None:
    fact = MemoryRecord(
        id="fact",
        scope="agent:matrix-bonus",
        type="fact",
        key="fact:test",
        title="Fact",
        content="plain fact",
        confidence=0.7,
        created_at="2026-03-28T00:00:00+00:00",
        updated_at="2026-03-28T00:00:00+00:00",
        meta={"bm25_score": 0.8},
    )
    insight = replace(
        fact,
        id="insight",
        type="meta_insight",
        key="insight:test",
        title="Insight",
    )
    scored = score_records([fact, insight])
    assert scored[0].id == "insight"


_CASES = [
    ("typed-memory-candidate-promotion", _case_typed_memory_candidate_promotion),
    ("ranking-contradiction", _case_ranking_contradiction),
    ("candidate-gc-confidence-decay", _case_candidate_gc_confidence_decay),
    ("reflection-confidence-decay", _case_reflection_confidence_decay),
    # ("type-threshold-correction", _case_type_aware_thresholds_correction) removed.
    ("preference-boost-cooldown", _case_preference_boost_cooldown),
    ("scope-isolation-reflection", _case_scope_isolation_reflection),
    ("supersession-reasons-keyed-upsert", _case_supersession_reasons_keyed_upsert),
    ("disuse-decay-capsule-retrieval", _case_disuse_decay_capsule_retrieval),
    ("meta-insight-type-bonus", _case_meta_insight_type_bonus),
]


@pytest.mark.parametrize(
    ("case_id", "case_fn"), _CASES, ids=[item[0] for item in _CASES]
)
def test_e2e_feature_interaction_matrix(case_id, case_fn, tmp_path) -> None:
    case_fn(tmp_path / case_id)
