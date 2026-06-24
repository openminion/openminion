from __future__ import annotations

from datetime import datetime, timezone

from tests.redteam.probes.mpd_feasibility_probe import (
    CorpusCandidate,
    MinimumViableGate,
    ProbeConfig,
)


def _candidate(
    *,
    candidate_id: str,
    submitted_at: datetime,
) -> CorpusCandidate:
    return CorpusCandidate(
        id=candidate_id,
        category="ratelimit",
        text="candidate",
        claim_key="fact:test",
        polarity="asserts",
        source_class="llm_extracted",
        submitted_at=submitted_at.isoformat(),
        expected_decision_minimum_viable="BLOCKED",
        expected_reason_code_minimum_viable="RATE_LIMITED",
        expected_decision_composite_v1="BLOCKED",
        expected_reason_code_composite_v1="RATE_LIMITED",
    )


def test_minimum_viable_gate_uses_shared_rate_limiter() -> None:
    config = ProbeConfig(
        pre_seeded_llm_extracted_in_saturated_window=1,
        rate_limit_llm_extracted_per_hour=1,
    )
    gate = MinimumViableGate(config)

    decision, reason = gate.decide(
        _candidate(
            candidate_id="probe-rate-limit",
            submitted_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc),
        )
    )

    assert decision == "BLOCKED"
    assert reason == "RATE_LIMITED"
