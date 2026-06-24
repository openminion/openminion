from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass(slots=True)
class PrefetchPredictor:
    """Rolling n-gram prediction model for next tool call."""

    _ngram_counts: dict[tuple[str, ...], dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int))
    )
    _window_size: int = 3
    correct: int = 0
    wrong: int = 0

    def observe(self, tool_sequence: list[str]) -> None:
        """Record observed tool sequence for prediction model."""
        for i in range(len(tool_sequence)):
            context = tuple(tool_sequence[max(0, i - self._window_size) : i])
            if context:
                self._ngram_counts[context][tool_sequence[i]] += 1

    def predict(self, recent_tools: list[str]) -> tuple[str | None, float]:
        """Predict next tool name and confidence."""
        context = tuple(recent_tools[-self._window_size :])
        if not context or context not in self._ngram_counts:
            return None, 0.0
        counts = self._ngram_counts[context]
        total = sum(counts.values())
        if total == 0:
            return None, 0.0
        best_tool = max(counts, key=counts.get)  # type: ignore[arg-type]
        confidence = counts[best_tool] / total
        return best_tool, confidence

    def record_outcome(self, predicted: str, actual: str) -> None:
        """Record whether the prediction matched the actual tool call."""
        if predicted == actual:
            self.correct += 1
        else:
            self.wrong += 1
