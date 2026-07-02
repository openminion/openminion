from .reasons import ReasonCode
from .schemas import (
    BudgetAdjust,
    MetaConfig,
    MetaDirective,
    MetaMetrics,
    MetaResult,
    MetaState,
)


class MetaRulesEngine:
    def __init__(self, cfg: MetaConfig | None = None) -> None:
        self.cfg: MetaConfig = cfg or MetaConfig()

    def evaluate(self, metrics: MetaMetrics) -> MetaResult:
        try:
            return self._evaluate(metrics)
        except (ValueError, TypeError) as exc:
            return self._fallback(metrics, ReasonCode.FALLBACK_INVALID_METRICS, exc)
        except Exception as exc:  # noqa: BLE001
            return self._fallback(metrics, ReasonCode.FALLBACK_EVALUATION_ERROR, exc)

    def _evaluate(self, metrics: MetaMetrics) -> MetaResult:
        if metrics.user_kill_requested:
            directive = MetaDirective(
                override_next_state="STOPPED",
                tool_temp_denylist=["*"],
                note_to_user="Safety stop enabled. No further actions will be taken.",
            )
            return self._make_result(
                MetaState.PANIC,
                directive,
                metrics,
                [ReasonCode.PANIC_USER_KILL],
            )

        recovery_reasons: list[ReasonCode] = []
        if metrics.recent_failures >= self.cfg.repeat_failure_threshold:
            recovery_reasons.append(ReasonCode.RECOVERY_REPEAT_ERROR)
        if metrics.loop_count >= self.cfg.loop_count_threshold:
            recovery_reasons.append(ReasonCode.RECOVERY_LOOP)
        if metrics.replan_count >= self.cfg.replan_count_threshold:
            recovery_reasons.append(ReasonCode.RECOVERY_REPLAN_LIMIT)
        if (
            metrics.ticks_without_progress >= self.cfg.low_progress_ticks_threshold
            and metrics.no_new_facts_streak
            >= self.cfg.low_progress_no_new_facts_threshold
        ):
            recovery_reasons.append(ReasonCode.RECOVERY_STALL)

        if recovery_reasons:
            directive = MetaDirective(
                override_next_state="PLAN",
                prompt_constraints=[
                    "Change strategy; do not repeat the same failing action."
                ],
            )
            return self._make_result(
                MetaState.RECOVERY, directive, metrics, recovery_reasons
            )

        ha_reasons: list[ReasonCode] = []
        if metrics.risk_class == "high":
            ha_reasons.append(ReasonCode.HIGH_ASSURANCE_RISK_CLASS)
        if metrics.risk_score >= self.cfg.high_risk_score_threshold:
            ha_reasons.append(ReasonCode.HIGH_ASSURANCE_RISK_SCORE)
        if metrics.irreversible:
            ha_reasons.append(ReasonCode.HIGH_ASSURANCE_IRREVERSIBLE)
        if metrics.grounding_confidence < self.cfg.low_grounding_threshold:
            ha_reasons.append(ReasonCode.HIGH_ASSURANCE_LOW_GROUNDING)
        if metrics.last_verify_outcome == "fail":
            ha_reasons.append(ReasonCode.HIGH_ASSURANCE_LOW_VERIFY_OUTCOME)
        if metrics.candidate_disagreement_score > 0.6:
            ha_reasons.append(ReasonCode.HIGH_ASSURANCE_CANDIDATE_DISAGREEMENT)

        if ha_reasons:
            directive = MetaDirective(
                tier_override="T3_high_assurance",
                require_confirmation=metrics.requires_side_effects,
                require_verification=True,
                verification_mode=self.cfg.high_risk_verification_mode,
                prompt_constraints=[
                    "Only claim what is supported by observations/facts."
                ],
            )
            return self._make_result(
                MetaState.HIGH_ASSURANCE, directive, metrics, ha_reasons
            )

        cautious_reasons: list[ReasonCode] = []
        if metrics.risk_class == "medium":
            cautious_reasons.append(ReasonCode.CAUTIOUS_MEDIUM_RISK_CLASS)
        if (
            metrics.needs_clarification
            or metrics.intent_confidence < self.cfg.low_intent_confidence_threshold
        ):
            cautious_reasons.append(
                ReasonCode.CAUTIOUS_NEEDS_CLARIFICATION
                if metrics.needs_clarification
                else ReasonCode.CAUTIOUS_LOW_INTENT_CONFIDENCE
            )
        if metrics.ambiguity_score >= self.cfg.high_ambiguity_threshold:
            cautious_reasons.append(ReasonCode.CAUTIOUS_HIGH_AMBIGUITY)
        if metrics.policy_recent_denies > 0:
            cautious_reasons.append(ReasonCode.CAUTIOUS_POLICY_DENIES)
        if metrics.tool_success_rate_ewma < self.cfg.tool_degraded_threshold:
            cautious_reasons.append(ReasonCode.CAUTIOUS_TOOL_DEGRADED)
        budget_pressure = 1.0 - metrics.budget_remaining
        if budget_pressure >= self.cfg.budget_pressure_threshold:
            cautious_reasons.append(ReasonCode.CAUTIOUS_BUDGET_PRESSURE)

        if cautious_reasons:
            directive = self._build_cautious_directive(
                metrics, cautious_reasons, budget_pressure
            )
            return self._make_result(
                MetaState.CAUTIOUS, directive, metrics, cautious_reasons
            )

        return self._make_result(
            MetaState.NORMAL,
            MetaDirective(),
            metrics,
            [ReasonCode.NORMAL_DEFAULT],
        )

    def _build_cautious_directive(
        self,
        metrics: MetaMetrics,
        reasons: list[ReasonCode],
        budget_pressure: float,
    ) -> MetaDirective:
        directive = MetaDirective(
            tier_override="T1_light",
            prompt_constraints=["State assumptions explicitly."],
        )

        if metrics.needs_clarification:
            directive.require_clarification = True
            directive.clarification_question = (
                "I need one clarification before proceeding. "
                "Could you confirm the exact target or intent?"
            )
            directive.override_next_state = "WAITING"
            directive.escalation_question = directive.clarification_question

        if metrics.requires_side_effects:
            directive.require_verification = True
            directive.verification_mode = self.cfg.medium_risk_verification_mode

        if ReasonCode.CAUTIOUS_BUDGET_PRESSURE in reasons:
            directive.budget_adjustments = BudgetAdjust(lower_context_limits=True)

        if ReasonCode.CAUTIOUS_TOOL_DEGRADED in reasons:
            directive.prompt_constraints.append(
                "Prefer read-only or non-destructive tool calls while tool health is degraded."
            )

        return directive

    def _make_result(
        self,
        state: MetaState,
        directive: MetaDirective,
        metrics: MetaMetrics,
        reasons: list[ReasonCode],
    ) -> MetaResult:
        return MetaResult(
            meta_state=state,
            directive=directive,
            metrics=metrics,
            reasons=[r.value for r in reasons],
            ruleset_version=self.cfg.ruleset_version,
        )

    def _fallback(
        self,
        metrics: MetaMetrics,
        code: ReasonCode,
        exc: Exception,
    ) -> MetaResult:
        directive = MetaDirective(
            require_confirmation=True,
            prompt_constraints=["Evaluation error — proceed conservatively."],
        )
        return MetaResult(
            meta_state=MetaState.CAUTIOUS,
            directive=directive,
            metrics=metrics,
            reasons=[code.value, f"exception_type:{type(exc).__name__}"],
            ruleset_version=self.cfg.ruleset_version,
        )
