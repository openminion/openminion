from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import pytest

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.runtime.candidate_readiness import (
    PromotionWeights,
    compute_promotion_readiness,
    extract_candidate_signals,
    score_candidate_from_config,
)
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
)
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _memory_config(*, auto_extract_enabled: bool = True):
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    return replace(
        cfg,
        candidate_learning=replace(
            cfg.candidate_learning,
            auto_extract_enabled=auto_extract_enabled,
            auto_extract_notify=True,
        ),
    )


def _make_adapter(*, auto_extract_enabled: bool = True):
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="candidate-agent",
        memory_config=_memory_config(auto_extract_enabled=auto_extract_enabled),
    )
    return store, service, adapter


def test_candidate_learning_defaults_enable_auto_extract() -> None:
    cfg = _memory_config()

    assert cfg.candidate_learning.auto_extract_enabled is True


def test_candidate_readiness_scores_signal_vector() -> None:
    weights = PromotionWeights(
        reconfirmation=1.0 / 6.0,
        retrieval_hits=1.0 / 6.0,
        survival=1.0 / 6.0,
        confidence=1.0 / 6.0,
        correction_resistance=1.0 / 6.0,
        outcome_utility=1.0 / 6.0,
    )

    score = compute_promotion_readiness(
        0.5,
        0.5,
        0.5,
        0.5,
        0.5,
        0.5,
        weights=weights,
    )

    assert score == pytest.approx(0.5)


def test_candidate_readiness_applies_trust_score_multiplicatively() -> None:
    weights = PromotionWeights(
        reconfirmation=1.0 / 6.0,
        retrieval_hits=1.0 / 6.0,
        survival=1.0 / 6.0,
        confidence=1.0 / 6.0,
        correction_resistance=1.0 / 6.0,
        outcome_utility=1.0 / 6.0,
    )

    score = compute_promotion_readiness(
        0.6,
        0.6,
        0.6,
        0.6,
        0.6,
        0.6,
        trust_score=0.5,
        weights=weights,
    )

    assert score == pytest.approx(0.3)


def test_extract_candidate_signals_uses_meta_and_timestamps() -> None:
    candidate = MemoryCandidate(
        candidate_id="cand-signal",
        session_id="session-a",
        proposed_scope="agent:candidate-agent",
        type="user_preference",
        title="Preference: dark mode",
        content="I prefer dark mode.",
        confidence=0.6,
        meta={"reconfirmation_count": 2, "retrieval_hit_count": 3},
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    signals = extract_candidate_signals(
        candidate,
        now="2026-03-27T00:00:00+00:00",
    )

    assert signals.reconfirmation == 1.0
    assert signals.retrieval_hits == 1.0
    assert signals.survival == pytest.approx(1.0, rel=1e-3)
    assert signals.confidence == 0.6
    assert signals.correction_resistance == 1.0
    assert signals.outcome_utility == 0.5


def test_extract_candidate_signals_rewards_success_path_and_keeps_legacy_neutral() -> (
    None
):
    success_candidate = MemoryCandidate(
        candidate_id="cand-success",
        session_id="session-a",
        proposed_scope="agent:candidate-agent",
        type="procedure",
        title="Procedure: deploy",
        content="Run rollback rehearsal before rollout.",
        confidence=0.6,
        meta={"source_success_path": True, "source_outcome_status": "success"},
        created_at="2026-03-20T00:00:00+00:00",
        updated_at="2026-03-20T00:00:00+00:00",
    )
    legacy_candidate = MemoryCandidate(
        candidate_id="cand-legacy",
        session_id="session-a",
        proposed_scope="agent:candidate-agent",
        type="procedure",
        title="Procedure: deploy",
        content="Run rollback rehearsal before rollout.",
        confidence=0.6,
        meta={},
        created_at="2026-03-20T00:00:00+00:00",
        updated_at="2026-03-20T00:00:00+00:00",
    )

    success_signals = extract_candidate_signals(
        success_candidate,
        now="2026-03-27T00:00:00+00:00",
    )
    legacy_signals = extract_candidate_signals(
        legacy_candidate,
        now="2026-03-27T00:00:00+00:00",
    )

    assert success_signals.outcome_utility == 0.75
    assert legacy_signals.outcome_utility == 0.5


def test_score_candidate_from_config_gives_success_provenance_advantage() -> None:
    _store, _service, adapter = _make_adapter()
    success_candidate = MemoryCandidate(
        candidate_id="cand-success",
        session_id="session-a",
        proposed_scope="agent:candidate-agent",
        type="procedure",
        title="Procedure: deploy",
        content="Run rollback rehearsal before rollout.",
        confidence=0.5,
        meta={
            "reconfirmation_count": 1,
            "retrieval_hit_count": 1,
            "source_success_path": True,
            "source_outcome_status": "success",
        },
        created_at="2026-03-20T00:00:00+00:00",
        updated_at="2026-03-20T00:00:00+00:00",
    )
    neutral_candidate = MemoryCandidate(
        candidate_id="cand-neutral",
        session_id="session-a",
        proposed_scope="agent:candidate-agent",
        type="procedure",
        title="Procedure: deploy",
        content="Run rollback rehearsal before rollout.",
        confidence=0.5,
        meta={"reconfirmation_count": 1, "retrieval_hit_count": 1},
        created_at="2026-03-20T00:00:00+00:00",
        updated_at="2026-03-20T00:00:00+00:00",
    )

    success_score = score_candidate_from_config(
        success_candidate,
        config=adapter._candidate_learning_config,  # noqa: SLF001
        now="2026-03-27T00:00:00+00:00",
    )
    neutral_score = score_candidate_from_config(
        neutral_candidate,
        config=adapter._candidate_learning_config,  # noqa: SLF001
        now="2026-03-27T00:00:00+00:00",
    )

    assert success_score > neutral_score


def test_promote_mature_candidates_uses_readiness_and_rejects_contradictions() -> None:
    _store, service, adapter = _make_adapter()
    service.candidate_put(
        MemoryCandidate(
            candidate_id="cand-promote",
            session_id="session-a",
            proposed_scope="agent:candidate-agent",
            type="user_preference",
            title="Preference: dark mode",
            content="I prefer dark mode.",
            confidence=0.4,
            claim_key="pref:dark_mode",
            source_class="user_input",
            meta={"reconfirmation_count": 2},
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )
    service.candidate_put(
        MemoryCandidate(
            candidate_id="cand-reject",
            session_id="session-a",
            proposed_scope="agent:candidate-agent",
            type="project_convention",
            title="Convention: pytest",
            content="We use pytest.",
            confidence=0.4,
            meta={"contradicted": True},
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )

    promoted = adapter._promote_mature_candidates(  # noqa: SLF001
        "session-a",
        user_message="",
        assistant_message="",
    )

    promoted_records = service.list(
        ListQueryOptions(
            scopes=["agent:candidate-agent"],
            types=["user_preference"],
            limit=10,
        )
    )
    rejected = service.candidate_get("cand-reject")
    assert promoted == 1
    assert len(promoted_records) == 1
    assert "dark mode" in str(promoted_records[0].content).lower()
    assert rejected is not None
    assert rejected.status == "rejected"


def test_promote_mature_candidates_blocks_low_trust_and_keeps_candidate_queryable() -> (
    None
):
    _store, service, adapter = _make_adapter()
    service.candidate_put(
        MemoryCandidate(
            candidate_id="cand-low-trust",
            session_id="session-a",
            proposed_scope="agent:candidate-agent",
            type="user_preference",
            title="Preference: dark mode",
            content="I prefer dark mode.",
            confidence=0.9,
            claim_key="pref:dark_mode",
            source_class="agent_inferred",
            meta={"reconfirmation_count": 2, "retrieval_hit_count": 3},
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )

    promoted = adapter._promote_mature_candidates(  # noqa: SLF001
        "session-a",
        user_message="",
        assistant_message="",
    )
    candidate = service.candidate_get("cand-low-trust")

    assert promoted == 0
    assert candidate is not None
    assert candidate.status == "proposed"
    assert candidate.meta["trust_gate_allowed"] is False
    assert candidate.meta["trust_gate_reason_code"] == "BELOW_TRUST_THRESHOLD"
    assert candidate.meta["trust_score"] == pytest.approx(0.4)
    assert candidate.meta["promotion_readiness_after_trust"] < 0.6


def test_promote_mature_candidates_allows_with_trust_explanation() -> None:
    _store, service, adapter = _make_adapter()
    _store.put(
        MemoryRecord(
            id="mem-existing",
            scope="agent:candidate-agent",
            type="user_preference",
            key="pref:existing",
            title="Preference: dark mode",
            content="I prefer dark mode.",
            source="validated",
            confidence=0.9,
            meta={"claim_key": "pref:dark_mode", "polarity": "asserts"},
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )
    service.candidate_put(
        MemoryCandidate(
            candidate_id="cand-trusted",
            session_id="session-a",
            proposed_scope="agent:candidate-agent",
            type="user_preference",
            title="Preference: dark mode",
            content="I prefer dark mode.",
            confidence=0.9,
            claim_key="pref:dark_mode",
            source_class="llm_extracted",
            meta={"reconfirmation_count": 2, "retrieval_hit_count": 3},
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )

    promoted = adapter._promote_mature_candidates(  # noqa: SLF001
        "session-a",
        user_message="",
        assistant_message="",
    )
    candidate = service.candidate_get("cand-trusted")

    assert promoted == 1
    assert candidate is not None
    assert candidate.status == "promoted"
    assert candidate.meta["trust_gate_allowed"] is True
    assert candidate.meta["trust_gate_reason_code"] == "ALLOWED"
    assert candidate.meta["trust_score"] == pytest.approx(0.75)
    assert candidate.meta["trust_peer_count"] == 1


def test_gc_candidates_rejects_old_low_readiness_candidates() -> None:
    _store, service, adapter = _make_adapter()
    service.candidate_put(
        MemoryCandidate(
            candidate_id="cand-old",
            session_id="session-a",
            proposed_scope="agent:candidate-agent",
            type="fact",
            title="Shell note",
            content="My shell is fish.",
            confidence=0.4,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )

    rejected = adapter._gc_candidates()  # noqa: SLF001
    candidate = service.candidate_get("cand-old")

    assert rejected == 1
    assert candidate is not None
    assert candidate.status == "rejected"
    assert candidate.review is not None
    assert candidate.review.note == "gc_expired"


def test_cross_session_candidate_listing_uses_proposed_scope() -> None:
    _store, service, _adapter = _make_adapter()
    service.candidate_put(
        MemoryCandidate(
            candidate_id="cand-s1",
            session_id="session-1",
            proposed_scope="agent:candidate-agent",
            type="user_preference",
            title="Preference: dark mode",
            content="I prefer dark mode.",
        )
    )
    service.candidate_put(
        MemoryCandidate(
            candidate_id="cand-s2",
            session_id="session-2",
            proposed_scope="agent:candidate-agent",
            type="user_preference",
            title="Preference: concise summaries",
            content="I prefer concise summaries.",
        )
    )

    candidates = service.candidate_list(
        CandidateListOptions(proposed_scope="agent:candidate-agent", status="proposed")
    )

    assert {candidate.session_id for candidate in candidates} == {
        "session-1",
        "session-2",
    }


def test_score_candidate_from_config_uses_phase4_threshold_inputs() -> None:
    _store, service, adapter = _make_adapter()
    candidate = MemoryCandidate(
        candidate_id="cand-score",
        session_id="session-a",
        proposed_scope="agent:candidate-agent",
        type="user_preference",
        title="Preference: dark mode",
        content="I prefer dark mode.",
        confidence=0.4,
        meta={"reconfirmation_count": 2, "retrieval_hit_count": 1},
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    score = score_candidate_from_config(
        candidate,
        config=adapter._candidate_learning_config,  # noqa: SLF001
        now="2026-03-27T00:00:00+00:00",
    )

    assert score >= 0.6
