from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.runtime.promotion import PromotionPolicy


def _candidate(
    *,
    proposed_scope: str,
    source: str,
    status: str,
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id="c1",
        session_id="s1",
        proposed_scope=proposed_scope,
        type="fact",
        content="test",
        source=source,
        status=status,
    )


def _evaluate(
    *,
    proposed_scope: str,
    source: str,
    status: str,
    target_scope: str,
):
    policy = PromotionPolicy(auto_promote_sources={"validated"})
    return policy.evaluate(
        _candidate(proposed_scope=proposed_scope, source=source, status=status),
        target_scope,
    )


def test_promotion_policy_decisions() -> None:
    for proposed_scope, source, status, target_scope, allowed in (
        ("global:all", "validated", "proposed", "global:all", False),
        ("global:all", "agent_inferred", "approved", "global:all", True),
        ("session:s1", "validated", "proposed", "session:s1", True),
        ("session:s1", "agent_inferred", "proposed", "session:s1", False),
    ):
        decision = _evaluate(
            proposed_scope=proposed_scope,
            source=source,
            status=status,
            target_scope=target_scope,
        )
        assert decision.allowed is allowed


def test_promotion_policy_denial_reason_mentions_approval() -> None:
    decision = _evaluate(
        proposed_scope="session:s1",
        source="agent_inferred",
        status="proposed",
        target_scope="session:s1",
    )
    assert "requires explicit approval" in decision.reason
