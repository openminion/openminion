from typing import Protocol, runtime_checkable

from .schemas import MetaMetrics, MetaResult


@runtime_checkable
class MetaEvaluatorProtocol(Protocol):
    """Contract for all metactl evaluator implementations."""

    def evaluate(self, metrics: MetaMetrics) -> MetaResult:
        """Evaluate the supplied runtime metrics and return a directive result."""
        ...
