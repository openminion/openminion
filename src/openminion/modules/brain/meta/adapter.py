from .interfaces import MetaEvaluatorProtocol
from .schemas import MetaMetrics, MetaResult


class CheckpointAdapter:
    _CHECKPOINT_TAG_PREFIX = "checkpoint"

    def __init__(self, evaluator: MetaEvaluatorProtocol) -> None:
        self._engine = evaluator

    def after_interpret(self, metrics: MetaMetrics) -> MetaResult:
        return self._evaluate_with_tag(metrics, "after_interpret")

    def before_plan(self, metrics: MetaMetrics) -> MetaResult:
        return self._evaluate_with_tag(metrics, "before_plan")

    def before_act(self, metrics: MetaMetrics) -> MetaResult:
        return self._evaluate_with_tag(metrics, "before_act")

    def after_observe(self, metrics: MetaMetrics) -> MetaResult:
        return self._evaluate_with_tag(metrics, "after_observe")

    def before_respond(self, metrics: MetaMetrics) -> MetaResult:
        return self._evaluate_with_tag(metrics, "before_respond")

    def _evaluate_with_tag(self, metrics: MetaMetrics, phase: str) -> MetaResult:
        result = self._engine.evaluate(metrics)
        tag = f"{self._CHECKPOINT_TAG_PREFIX}:{phase}"
        updated_reasons = list(result.reasons) + [tag]
        return MetaResult(
            meta_state=result.meta_state,
            directive=result.directive,
            metrics=result.metrics,
            reasons=updated_reasons,
            ruleset_version=result.ruleset_version,
        )
