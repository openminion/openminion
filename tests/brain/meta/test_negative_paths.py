from __future__ import annotations

from pydantic import ValidationError

from openminion.modules.brain.meta.evaluator import MetaRulesEngine
from openminion.modules.brain.meta.schemas import (
    MetaDirective,
    MetaMetrics,
    MetaResult,
    MetaState,
)


def _fallback_result(reason_code, exc: Exception):
    return MetaRulesEngine()._fallback(MetaMetrics(), reason_code, exc)


def test_invalid_metrics_input_rejection() -> None:
    for factory in (
        lambda: MetaMetrics(definitely_not_a_real_field="oops"),  # type: ignore[call-arg]
        lambda: MetaMetrics(risk_class="catastrophic"),  # type: ignore[arg-type]
        lambda: MetaDirective(override_next_state="FLYING"),  # type: ignore[arg-type]
        lambda: MetaDirective(tier_override="T9_impossible"),  # type: ignore[arg-type]
        lambda: MetaDirective(not_a_field=True),  # type: ignore[call-arg]
        lambda: MetaMetrics(last_verify_outcome="maybe"),  # type: ignore[arg-type]
    ):
        try:
            factory()
        except ValidationError:
            pass
        else:
            raise AssertionError("expected ValidationError")


def test_evaluator_never_raises_on_valid_metrics() -> None:
    engine = MetaRulesEngine()
    for metrics in (
        MetaMetrics(risk_score=100, recent_failures=999, loop_count=999),
        MetaMetrics(intent_confidence=0.0, grounding_confidence=0.0),
        MetaMetrics(budget_remaining=0.0),
        MetaMetrics(recent_state_path=["INTERPRET"] * 100),
    ):
        assert isinstance(engine.evaluate(metrics), MetaResult)


def test_fallback_directive_is_conservative() -> None:
    from openminion.modules.brain.meta.reasons import ReasonCode

    fallback_result = _fallback_result(
        ReasonCode.FALLBACK_EVALUATION_ERROR, RuntimeError("sim")
    )
    assert fallback_result.meta_state == MetaState.CAUTIOUS
    assert fallback_result.directive.require_confirmation
    assert ReasonCode.FALLBACK_EVALUATION_ERROR.value in fallback_result.reasons


def test_fallback_always_returns_complete_meta_result() -> None:
    from openminion.modules.brain.meta.reasons import ReasonCode

    result = _fallback_result(ReasonCode.FALLBACK_INVALID_METRICS, ValueError("bad"))
    MetaResult.model_validate(result.model_dump())


def test_fallback_has_reason_codes_and_exception_hints() -> None:
    from openminion.modules.brain.meta.reasons import ReasonCode

    result = _fallback_result(ReasonCode.FALLBACK_EVALUATION_ERROR, ValueError("test"))
    assert result.reasons
    assert any(
        "ValueError" in reason or "exception_type" in reason
        for reason in result.reasons
    )


def test_edge_case_inputs() -> None:
    assert (
        MetaRulesEngine().evaluate(MetaMetrics(risk_score=999)).meta_state
        == MetaState.HIGH_ASSURANCE
    )
    assert (
        MetaRulesEngine().evaluate(MetaMetrics(recent_failures=-99)).meta_state
        == MetaState.NORMAL
    )
    assert (
        MetaRulesEngine()
        .evaluate(
            MetaMetrics(
                recent_failures=0,
                loop_count=0,
                replan_count=0,
                risk_score=0,
                risk_class="low",
            )
        )
        .meta_state
        == MetaState.NORMAL
    )
    assert (
        MetaRulesEngine().evaluate(MetaMetrics(budget_remaining=0.0)).meta_state
        == MetaState.CAUTIOUS
    )
    assert (
        MetaRulesEngine()
        .evaluate(
            MetaMetrics(
                user_kill_requested=True,
                risk_class="high",
                risk_score=100,
                irreversible=True,
                recent_failures=100,
                loop_count=100,
            )
        )
        .meta_state
        == MetaState.PANIC
    )


def test_precedence_ordering() -> None:
    assert (
        MetaRulesEngine()
        .evaluate(MetaMetrics(recent_failures=5, risk_class="high", risk_score=90))
        .meta_state
        == MetaState.RECOVERY
    )
    assert (
        MetaRulesEngine()
        .evaluate(MetaMetrics(risk_class="high", needs_clarification=True))
        .meta_state
        == MetaState.HIGH_ASSURANCE
    )
    assert (
        MetaRulesEngine().evaluate(MetaMetrics(risk_class="medium")).meta_state
        == MetaState.CAUTIOUS
    )
