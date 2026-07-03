from __future__ import annotations

import pytest

from openminion.modules.brain.meta.schemas import (
    BudgetAdjust,
    MetaConfig,
    MetaDirective,
    MetaMetrics,
    MetaResult,
    MetaState,
    VerificationMode,
)


class TestMetaStateEnum:
    def test_all_states_present(self) -> None:
        states = {s.value for s in MetaState}
        assert states == {"NORMAL", "CAUTIOUS", "HIGH_ASSURANCE", "RECOVERY", "PANIC"}


class TestVerificationModeEnum:
    def test_all_modes_present(self) -> None:
        modes = {m.value for m in VerificationMode}
        assert modes == {"none", "rule_based", "second_opinion", "panel_judge"}


class TestMetaMetrics:
    def test_defaults(self) -> None:
        m = MetaMetrics()
        assert m.risk_class == "low"
        assert m.risk_score == 0
        assert m.intent_confidence == pytest.approx(0.7)
        assert m.grounding_confidence == pytest.approx(1.0)
        assert m.budget_remaining == pytest.approx(1.0)
        assert m.unknown_fields_count == 0
        assert m.steps_completed_recent == 0
        assert m.contradiction_flags == []
        assert m.candidate_disagreement_score == pytest.approx(0.0)
        assert not m.requires_evidence_only
        assert m.tool_timeout_count_recent == 0
        assert m.tool_auth_error_count_recent == 0
        assert m.llm_calls_used == 0
        assert m.llm_calls_max == 8
        assert m.tool_calls_used == 0
        assert m.tool_calls_max == 8
        assert m.budget_pressure == pytest.approx(0.0)
        assert not m.user_corrected_me_recently
        assert not m.user_requested_thoroughness
        assert not m.user_requested_brevity
        assert not m.user_kill_requested
        assert not m.needs_clarification
        assert not m.irreversible

    def test_clamp_float_over(self) -> None:
        m = MetaMetrics(intent_confidence=9.0)
        assert m.intent_confidence == pytest.approx(1.0)

    def test_clamp_float_under(self) -> None:
        m = MetaMetrics(grounding_confidence=-5.0)
        assert m.grounding_confidence == pytest.approx(0.0)

    def test_clamp_risk_score_over(self) -> None:
        m = MetaMetrics(risk_score=999)
        assert m.risk_score == 100

    def test_clamp_risk_score_under(self) -> None:
        m = MetaMetrics(risk_score=-10)
        assert m.risk_score == 0

    def test_clamp_non_negative_int(self) -> None:
        m = MetaMetrics(recent_failures=-3)
        assert m.recent_failures == 0
        m2 = MetaMetrics(unknown_fields_count=-2, llm_calls_used=-1)
        assert m2.unknown_fields_count == 0
        assert m2.llm_calls_used == 0

    def test_extended_fields_round_trip(self) -> None:
        metrics = MetaMetrics(
            unknown_fields_count=2,
            steps_completed_recent=4,
            contradiction_flags=["a", "b"],
            candidate_disagreement_score=0.61,
            requires_evidence_only=True,
            tool_timeout_count_recent=1,
            tool_auth_error_count_recent=1,
            llm_calls_used=3,
            llm_calls_max=9,
            tool_calls_used=2,
            tool_calls_max=7,
            budget_pressure=0.45,
            user_corrected_me_recently=True,
            user_requested_thoroughness=True,
            user_requested_brevity=False,
        )
        restored = MetaMetrics.model_validate(metrics.model_dump())
        assert restored.unknown_fields_count == 2
        assert restored.steps_completed_recent == 4
        assert restored.contradiction_flags == ["a", "b"]
        assert restored.candidate_disagreement_score == pytest.approx(0.61)
        assert restored.requires_evidence_only
        assert restored.tool_timeout_count_recent == 1
        assert restored.tool_auth_error_count_recent == 1
        assert restored.llm_calls_used == 3
        assert restored.llm_calls_max == 9
        assert restored.tool_calls_used == 2
        assert restored.tool_calls_max == 7
        assert restored.budget_pressure == pytest.approx(0.45)
        assert restored.user_corrected_me_recently
        assert restored.user_requested_thoroughness
        assert not restored.user_requested_brevity

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(Exception):
            MetaMetrics(unknown_extra_field="bad")  # type: ignore[call-arg]

    def test_risk_class_literal(self) -> None:
        with pytest.raises(Exception):
            MetaMetrics(risk_class="catastrophic")  # type: ignore[arg-type]


class TestMetaDirective:
    def test_defaults(self) -> None:
        d = MetaDirective()
        assert d.override_next_state is None
        assert d.tier_override is None
        assert not d.require_confirmation
        assert not d.require_verification
        assert d.verification_mode is VerificationMode.none
        assert d.tool_temp_denylist == []
        assert d.prompt_constraints == []

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(Exception):
            MetaDirective(not_a_field=True)  # type: ignore[call-arg]

    def test_override_next_state_literal(self) -> None:
        d = MetaDirective(override_next_state="STOPPED")
        assert d.override_next_state == "STOPPED"
        with pytest.raises(Exception):
            MetaDirective(override_next_state="INVALID")  # type: ignore[arg-type]


class TestBudgetAdjust:
    def test_defaults(self) -> None:
        b = BudgetAdjust()
        assert not b.lower_context_limits
        assert not b.raise_context_limits
        assert b.lower_llm_calls_max is None

    def test_extra_forbidden(self) -> None:
        with pytest.raises(Exception):
            BudgetAdjust(unknown=True)  # type: ignore[call-arg]


class TestMetaResult:
    def test_round_trip_serialization(self) -> None:
        metrics = MetaMetrics(risk_class="high", risk_score=80)
        directive = MetaDirective(
            require_verification=True, tier_override="T3_high_assurance"
        )
        result = MetaResult(
            meta_state=MetaState.HIGH_ASSURANCE,
            directive=directive,
            metrics=metrics,
            reasons=["HIGH_ASSURANCE_RISK_CLASS"],
            ruleset_version="metactl.v1",
        )
        data = result.model_dump()
        restored = MetaResult.model_validate(data)
        assert restored.meta_state is MetaState.HIGH_ASSURANCE
        assert restored.directive.require_verification
        assert restored.metrics.risk_score == 80
        assert "HIGH_ASSURANCE_RISK_CLASS" in restored.reasons

    def test_ruleset_version_default(self) -> None:
        result = MetaResult(
            meta_state=MetaState.NORMAL,
            directive=MetaDirective(),
            metrics=MetaMetrics(),
        )
        assert result.ruleset_version == "metactl.v1"


class TestMetaConfig:
    def test_defaults(self) -> None:
        cfg = MetaConfig()
        assert cfg.ruleset_version == "metactl.v1"
        assert cfg.high_risk_score_threshold == 70
        assert cfg.low_grounding_threshold == pytest.approx(0.5)
        assert cfg.repeat_failure_threshold == 2

    def test_extra_forbidden(self) -> None:
        with pytest.raises(Exception):
            MetaConfig(bad_field=True)  # type: ignore[call-arg]
