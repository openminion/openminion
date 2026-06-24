from __future__ import annotations

from openminion.modules.brain.meta.evaluator import MetaRulesEngine
from openminion.modules.brain.meta.schemas import MetaMetrics, MetaState


def test_five_tier_state_snapshots() -> None:
    engine = MetaRulesEngine()

    cases = [
        ("normal", MetaMetrics(), MetaState.NORMAL),
        ("cautious", MetaMetrics(risk_class="medium"), MetaState.CAUTIOUS),
        ("high_assurance", MetaMetrics(risk_class="high"), MetaState.HIGH_ASSURANCE),
        ("recovery", MetaMetrics(recent_failures=3), MetaState.RECOVERY),
        ("panic", MetaMetrics(user_kill_requested=True), MetaState.PANIC),
    ]

    snapshot: dict[str, dict[str, object]] = {}
    for label, metrics, expected_state in cases:
        result = engine.evaluate(metrics)
        snapshot[label] = {
            "state": result.meta_state.value,
            "reasons": list(result.reasons),
            "ruleset_version": result.ruleset_version,
        }
        assert result.meta_state == expected_state
        assert result.reasons

    assert snapshot == {
        "normal": {
            "state": "NORMAL",
            "reasons": ["NORMAL_DEFAULT"],
            "ruleset_version": "metactl.v1",
        },
        "cautious": {
            "state": "CAUTIOUS",
            "reasons": ["CAUTIOUS_MEDIUM_RISK_CLASS"],
            "ruleset_version": "metactl.v1",
        },
        "high_assurance": {
            "state": "HIGH_ASSURANCE",
            "reasons": ["HIGH_ASSURANCE_RISK_CLASS"],
            "ruleset_version": "metactl.v1",
        },
        "recovery": {
            "state": "RECOVERY",
            "reasons": ["RECOVERY_REPEAT_ERROR"],
            "ruleset_version": "metactl.v1",
        },
        "panic": {
            "state": "PANIC",
            "reasons": ["PANIC_USER_KILL"],
            "ruleset_version": "metactl.v1",
        },
    }


def test_panic_precedence_negative_guard() -> None:
    engine = MetaRulesEngine()
    result = engine.evaluate(
        MetaMetrics(
            user_kill_requested=True,
            risk_class="high",
            recent_failures=99,
            loop_count=99,
        )
    )
    assert result.meta_state == MetaState.PANIC
    assert "PANIC_USER_KILL" in result.reasons
