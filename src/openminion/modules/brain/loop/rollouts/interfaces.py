"""PRV protocol contracts for scoring and selecting rollouts."""

from typing import Protocol, Sequence

from openminion.modules.brain.loop.rollouts.schemas import (
    RolloutPlan,
    RolloutResult,
)


class RolloutScorer(Protocol):
    """Post-generation quality scorer."""

    def score(
        self, result: RolloutResult, plan: RolloutPlan
    ) -> float:  # pragma: no cover - Protocol
        ...


class RolloutSelector(Protocol):
    """Selects the winning rollout from a list of results."""

    def select(
        self, results: Sequence[RolloutResult]
    ) -> RolloutResult:  # pragma: no cover - Protocol
        ...
