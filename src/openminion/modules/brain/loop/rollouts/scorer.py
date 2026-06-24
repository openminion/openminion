"""Rollout scoring implementations."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from openminion.modules.brain.loop.rollouts.schemas import (
    RolloutPlan,
    RolloutResult,
)


class _SmallModelClient(Protocol):
    def call(
        self, *, prompt: str, timeout_seconds: int
    ) -> dict[str, Any]:  # pragma: no cover - structural
        ...


def _clamp_unit(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


@dataclass
class StubRolloutScorer:
    """Deterministic stub scorer for tests."""

    scorer_fn: Callable[[RolloutResult, RolloutPlan], float]

    def score(self, result: RolloutResult, plan: RolloutPlan) -> float:
        return _clamp_unit(self.scorer_fn(result, plan))


@dataclass
class LLMRolloutScorer:
    """LLM-backed scorer that parses a unit-clamped score."""

    client: _SmallModelClient
    model_name: str = "claude-haiku-3.5"
    timeout_seconds: int = 3

    def score(self, result: RolloutResult, plan: RolloutPlan) -> float:
        prompt = self._format_prompt(result, plan)
        try:
            response = self.client.call(
                prompt=prompt, timeout_seconds=self.timeout_seconds
            )
        except Exception:
            return 0.0
        raw = response.get("quality_score", 0.0)
        try:
            return _clamp_unit(float(raw))
        except (TypeError, ValueError):
            return 0.0

    def _format_prompt(self, result: RolloutResult, plan: RolloutPlan) -> str:
        return (
            "[ROLLOUT SCORER]\n"
            f"step_id: {plan.step_id}\n"
            f"scorer_id: {plan.scorer_id}\n"
            f"rollout_id: {result.rollout_id}\n"
            f"output: {result.output}\n"
            "Return a JSON object with key `quality_score` in [0, 1]."
        )
