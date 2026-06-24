from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.trust import RateLimitDecision
from openminion.modules.memory.trust.scorer import compute_trust_score


def _candidate(**overrides: object) -> MemoryCandidate:
    payload: dict[str, object] = {
        "candidate_id": "cand-1",
        "session_id": "session-1",
        "proposed_scope": "agent:test",
        "type": "user_preference",
        "title": "Preference",
        "content": "I prefer dark mode.",
        "confidence": 0.7,
        "claim_key": "pref:dark_mode",
        "polarity": "asserts",
        "source_class": "llm_extracted",
        "meta": {},
        "created_at": "2026-05-21T12:00:00+00:00",
        "updated_at": "2026-05-21T12:00:00+00:00",
    }
    payload.update(overrides)
    return MemoryCandidate(**payload)


def _window(
    *,
    observed_promotions: int,
    max_promotions: int | None,
) -> RateLimitDecision:
    return RateLimitDecision(
        allowed=True,
        reason_code="ALLOWED",
        observed_promotions=observed_promotions,
        max_promotions=max_promotions,
    )


def test_compute_trust_score_uses_source_provenance_table() -> None:
    result = compute_trust_score(
        _candidate(source_class="tool_result"),
        repo=[],
        source_window=_window(observed_promotions=0, max_promotions=100),
    )

    assert result.source_class == "tool_result"
    assert result.trust_score.source_provenance == 0.8
    assert result.reason_code == "ALLOWED"


def test_compute_trust_score_counts_exact_match_same_polarity_peers() -> None:
    repo = [
        SimpleNamespace(
            id="r1",
            meta={"claim_key": "pref:dark_mode", "polarity": "asserts"},
            is_deleted=False,
        ),
        SimpleNamespace(
            id="r2",
            meta={"claim_key": "pref:dark_mode", "polarity": "asserts"},
            is_deleted=False,
        ),
        SimpleNamespace(
            id="r3",
            meta={"claim_key": "pref:dark_mode", "polarity": "negates"},
            is_deleted=False,
        ),
    ]

    result = compute_trust_score(
        _candidate(),
        repo=repo,
        source_window=_window(observed_promotions=0, max_promotions=50),
    )

    assert result.peer_count == 2
    assert result.trust_score.corroboration == 1.0
    assert result.trust_score.score == 1.0


def test_compute_trust_score_uses_rate_limit_pressure() -> None:
    result = compute_trust_score(
        _candidate(source_class="agent_inferred"),
        repo=[],
        source_window=_window(observed_promotions=15, max_promotions=30),
    )

    assert result.trust_score.rate_limit_pressure == 0.5
    assert result.trust_score.score == 0.2


def test_compute_trust_score_fails_closed_when_claim_key_missing() -> None:
    result = compute_trust_score(
        _candidate(claim_key=None, meta={}),
        repo=[],
        source_window=_window(observed_promotions=0, max_promotions=50),
    )

    assert result.reason_code == "MISSING_CLAIM_KEY"
    assert result.peer_count == 0
    assert result.trust_score.score == 0.0
    assert result.trust_score.corroboration == 0.0


def test_contradiction_penalty_is_zero_without_opposing_peers() -> None:
    result = compute_trust_score(
        _candidate(),
        repo=[],
        source_window=_window(observed_promotions=0, max_promotions=50),
    )

    assert result.trust_score.contradiction_penalty == 0.0


def test_contradiction_penalty_counts_currently_valid_opposing_peer() -> None:
    repo = [
        SimpleNamespace(
            id="r1",
            confidence=0.9,
            event_time="2026-05-21T00:00:00+00:00",
            valid_to=None,
            meta={"claim_key": "pref:dark_mode", "polarity": "negates"},
            is_deleted=False,
        )
    ]

    result = compute_trust_score(
        _candidate(),
        repo=repo,
        source_window=_window(observed_promotions=0, max_promotions=50),
    )

    assert result.trust_score.contradiction_penalty > 0.0


def test_contradiction_penalty_ignores_invalidated_opposing_peer() -> None:
    past_valid_to = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    repo = [
        SimpleNamespace(
            id="r1",
            confidence=0.9,
            event_time="2026-05-21T00:00:00+00:00",
            valid_to=past_valid_to,
            meta={"claim_key": "pref:dark_mode", "polarity": "negates"},
            is_deleted=False,
        )
    ]

    result = compute_trust_score(
        _candidate(),
        repo=repo,
        source_window=_window(observed_promotions=0, max_promotions=50),
    )

    assert result.trust_score.contradiction_penalty == 0.0


def test_contradiction_penalty_saturates_with_multiple_opposing_peers() -> None:
    repo = [
        SimpleNamespace(
            id="r1",
            confidence=1.0,
            event_time="2026-05-22T00:00:00+00:00",
            valid_to=None,
            meta={"claim_key": "pref:dark_mode", "polarity": "negates"},
            is_deleted=False,
        ),
        SimpleNamespace(
            id="r2",
            confidence=1.0,
            event_time="2026-05-22T00:00:00+00:00",
            valid_to=None,
            meta={"claim_key": "pref:dark_mode", "polarity": "negates"},
            is_deleted=False,
        ),
    ]

    result = compute_trust_score(
        _candidate(),
        repo=repo,
        source_window=_window(observed_promotions=0, max_promotions=50),
    )

    assert result.trust_score.contradiction_penalty == 1.0


def test_contradiction_penalty_ignores_same_polarity_peers() -> None:
    repo = [
        SimpleNamespace(
            id="r1",
            confidence=0.9,
            event_time="2026-05-22T00:00:00+00:00",
            valid_to=None,
            meta={"claim_key": "pref:dark_mode", "polarity": "asserts"},
            is_deleted=False,
        )
    ]

    result = compute_trust_score(
        _candidate(),
        repo=repo,
        source_window=_window(observed_promotions=0, max_promotions=50),
    )

    assert result.trust_score.contradiction_penalty == 0.0
