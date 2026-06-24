from dataclasses import dataclass

from openminion.modules.memory.constants import MEMORY_CANDIDATE_STATUS_APPROVED
from openminion.modules.memory.models import MemoryCandidate, MemoryScope


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None


class PromotionPolicy:
    """Evaluates whether a candidate can be promoted to a target scope."""

    def __init__(self, auto_promote_sources: set[str] | None = None):
        self.auto_promote_sources = auto_promote_sources or {"system", "validated"}

    def evaluate(self, candidate: MemoryCandidate, target_scope: str) -> PolicyDecision:
        if candidate.status == MEMORY_CANDIDATE_STATUS_APPROVED:
            return PolicyDecision(True)

        parsed_scope = MemoryScope.parse(target_scope)

        if parsed_scope.is_project:
            if candidate.source in {"validated", "user_said"}:
                return PolicyDecision(True)
            return PolicyDecision(
                False,
                f"Target scope {target_scope!r} requires explicit approval for source {candidate.source!r}",
            )

        if parsed_scope.is_global:
            if candidate.source == "system":
                return PolicyDecision(True)
            return PolicyDecision(
                False,
                f"Target scope {target_scope!r} requires explicit approval for source {candidate.source!r}",
            )

        if candidate.source in self.auto_promote_sources:
            return PolicyDecision(True)

        return PolicyDecision(
            False,
            f"Source {candidate.source!r} requires explicit approval for auto-promotion",
        )
