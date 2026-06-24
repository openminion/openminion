from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.runtime.consolidation.coordinator import (
    ConsolidationConfig,
)
from openminion.modules.memory.runtime.consolidation.eligibility import (
    ConsolidationEligibilityChecker,
)
from openminion.modules.memory.storage.memory import InMemoryMemoryStore


def _candidate(
    candidate_id: str,
    *,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        session_id="session-1",
        proposed_scope="agent:agent-1",
        type="fact",
        title=f"Candidate {candidate_id}",
        content=f"payload {candidate_id}",
        confidence=0.5,
        created_at=created_at,
        updated_at=updated_at,
    )


def test_consolidation_eligibility_reports_no_recent_rollout() -> None:
    checker = ConsolidationEligibilityChecker(InMemoryMemoryStore())

    result = checker.is_eligible(
        "session-1",
        "agent-1",
        ConsolidationConfig(),
        now=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
    )

    assert result.is_eligible is False
    assert result.reason_code == "NO_RECENT_ROLLOUT"


def test_consolidation_eligibility_reports_idle_gate_not_met() -> None:
    now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    store = InMemoryMemoryStore()
    store.candidate_put(
        _candidate(
            "cand-1",
            created_at=(now - timedelta(minutes=10)).isoformat(),
            updated_at=(now - timedelta(minutes=10)).isoformat(),
        )
    )
    checker = ConsolidationEligibilityChecker(store)

    result = checker.is_eligible(
        "session-1",
        "agent-1",
        ConsolidationConfig(idle_seconds_before_eligible=3600),
        now=now,
    )

    assert result.is_eligible is False
    assert result.reason_code == "IDLE_GATE_NOT_MET"


def test_consolidation_eligibility_reports_rate_limit_insufficient() -> None:
    now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    store = InMemoryMemoryStore()
    store.candidate_put(
        _candidate(
            "cand-1",
            created_at=(now - timedelta(hours=7)).isoformat(),
            updated_at=(now - timedelta(hours=7)).isoformat(),
        )
    )
    checker = ConsolidationEligibilityChecker(
        store,
        rate_limit_remaining_percent_probe=lambda session_id, agent_id: 10,
    )

    result = checker.is_eligible(
        "session-1",
        "agent-1",
        ConsolidationConfig(min_rate_limit_remaining_percent=25),
        now=now,
    )

    assert result.is_eligible is False
    assert result.reason_code == "RATE_LIMIT_INSUFFICIENT"


def test_consolidation_eligibility_reports_already_consolidated() -> None:
    now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    store = InMemoryMemoryStore()
    candidate = _candidate(
        "cand-1",
        created_at=(now - timedelta(hours=7)).isoformat(),
        updated_at=(now - timedelta(hours=7)).isoformat(),
    )
    store.candidate_put(candidate)
    checker = ConsolidationEligibilityChecker(
        store,
        working_state_probe=lambda session_id, agent_id: {
            "module_state": {
                "memory_context_maintenance": {
                    "last_consolidation_state_hash": (
                        "013366ce32845ab48d602c7c7e03a876fe3d1f4dad4fc08393b6d628ad18cec3"
                    )
                }
            }
        },
    )

    result = checker.is_eligible(
        "session-1",
        "agent-1",
        ConsolidationConfig(),
        now=now,
    )

    assert result.is_eligible is False
    assert result.reason_code == "ALREADY_CONSOLIDATED"


def test_consolidation_eligibility_reports_ok() -> None:
    now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    store = InMemoryMemoryStore()
    store.candidate_put(
        _candidate(
            "cand-1",
            created_at=(now - timedelta(hours=7)).isoformat(),
            updated_at=(now - timedelta(hours=7)).isoformat(),
        )
    )
    checker = ConsolidationEligibilityChecker(
        store,
        rate_limit_remaining_percent_probe=lambda session_id, agent_id: 80,
        working_state_probe=lambda session_id, agent_id: {
            "module_state": {"memory_context_maintenance": {}}
        },
    )

    result = checker.is_eligible(
        "session-1",
        "agent-1",
        ConsolidationConfig(),
        now=now,
    )

    assert result.is_eligible is True
    assert result.reason_code == "OK"
    assert result.state_hash
