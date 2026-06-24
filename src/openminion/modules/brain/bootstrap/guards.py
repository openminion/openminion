from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.schemas import Decision, WorkingState
from .recovery import _respond_decision


def _tier_0_restriction_decision(
    *,
    logger: CanonicalEventLogger,
    state: WorkingState,
    blocked_mode: str,
) -> Decision:
    logger.emit(
        "tier.blocked",
        {
            "tier": state.tier,
            "blocked_mode": blocked_mode,
            "reason": "T0 prohibits tools and planning.",
        },
        trace_id=state.trace_id,
    )
    return _respond_decision(
        confidence=1.0,
        reason_code="tier_0_restriction",
        answer=(
            "This profile is restricted to direct responses without tools or "
            "planning, so the requested action is unavailable on this turn."
        ),
    )
