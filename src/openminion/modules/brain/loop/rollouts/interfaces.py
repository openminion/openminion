from typing import Protocol, Sequence

from openminion.modules.brain.loop.rollouts.schemas import (
    RolloutPlan,
    RolloutResult,
)


class RolloutScorer(Protocol):
    def score(
        self, result: RolloutResult, plan: RolloutPlan
    ) -> float:  # pragma: no cover - Protocol
        ...


class RolloutSelector(Protocol):
    def select(
        self, results: Sequence[RolloutResult]
    ) -> RolloutResult:  # pragma: no cover - Protocol
        ...
