from __future__ import annotations

import unittest
from types import SimpleNamespace

from openminion.modules.brain.meta import MetaConfig, build_meta_metrics, evaluate_meta
from openminion.modules.brain.schemas import (
    ActionError,
    ActionResult,
    BudgetCounters,
    ClarifyQuestion,
    Plan,
    RespondDecision,
    ToolCommand,
    WorkingState,
)
from openminion.modules.brain.meta import MetaConfig as CanonicalMetaConfig
from openminion.modules.brain.meta import MetaRulesEngine as CanonicalMetaRulesEngine
from openminion.modules.brain.meta.schemas import MetaMetrics as CanonicalMetaMetrics


class _CaptureLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str | None]] = []

    def emit(self, event_type: str, payload: dict, trace_id: str | None = None) -> None:
        self.events.append((event_type, payload, trace_id))


def _base_state() -> WorkingState:
    step_read = ToolCommand(
        title="List files",
        tool_name="file.list_dir",
        args={"path": "."},
        success_criteria={"status": "success"},
        idempotency_key="msea-read-1",
        risk_level="low",
    )
    step_exec = ToolCommand(
        title="Run command",
        tool_name="exec.run",
        args={"command": "echo hello"},
        success_criteria={"status": "success"},
        idempotency_key="msea-exec-1",
        risk_level="med",
    )
    return WorkingState(
        session_id="msea-session",
        agent_id="msea-agent",
        trace_id="msea-trace",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=5,
            a2a_calls=2,
            tokens=600,
            time_ms=7000,
        ),
        llm_calls_used=3,
        llm_calls_max=10,
        plan=Plan(
            objective="characterize",
            steps=[step_read, step_exec],
            stop_conditions=[],
            assumptions=[],
            risk_summary="",
            success_criteria={},
        ),
        cursor=1,
        retries_for_step={"step-1": 2, "step-2": 1},
        open_questions=["missing target", "units?"],
        last_result=ActionResult(
            command_id="step-2",
            status="failed",
            outputs={},
            error=ActionError(code="AUTH_DENIED", message="denied"),
        ),
    )


class BuildMetaMetricsCharacterizationTests(unittest.TestCase):
    def test_build_meta_metrics_deterministic_snapshot(self) -> None:
        state = _base_state()
        budget_caps = BudgetCounters(
            ticks=20,
            tool_calls=8,
            a2a_calls=4,
            tokens=1000,
            time_ms=10000,
        )
        decision = RespondDecision(
            confidence=0.91,
            reason_code="characterize",
            respond_kind="answer",
            answer="ok",
        )

        metrics = build_meta_metrics(
            state=state,
            budget_caps=budget_caps,
            decision=decision,
            cfg=MetaConfig(),
        )
        self.assertIsInstance(metrics, CanonicalMetaMetrics)

        snapshot = {
            "intent_confidence": metrics.intent_confidence,
            "unknown_fields_count": metrics.unknown_fields_count,
            "ambiguity_score": metrics.ambiguity_score,
            "needs_clarification": metrics.needs_clarification,
            "risk_score": metrics.risk_score,
            "risk_class": metrics.risk_class,
            "irreversible": metrics.irreversible,
            "requires_side_effects": metrics.requires_side_effects,
            "steps_completed_recent": metrics.steps_completed_recent,
            "loop_count": metrics.loop_count,
            "recent_failures": metrics.recent_failures,
            "replan_count": metrics.replan_count,
            "grounding_confidence": metrics.grounding_confidence,
            "requires_evidence_only": metrics.requires_evidence_only,
            "tool_success_rate_ewma": metrics.tool_success_rate_ewma,
            "tool_timeout_count_recent": metrics.tool_timeout_count_recent,
            "tool_auth_error_count_recent": metrics.tool_auth_error_count_recent,
            "llm_calls_used": metrics.llm_calls_used,
            "llm_calls_max": metrics.llm_calls_max,
            "tool_calls_used": metrics.tool_calls_used,
            "tool_calls_max": metrics.tool_calls_max,
            "budget_remaining": metrics.budget_remaining,
            "budget_pressure": metrics.budget_pressure,
        }

        self.assertEqual(
            snapshot,
            {
                "intent_confidence": 0.91,
                "unknown_fields_count": 0,
                "ambiguity_score": 0.0,
                "needs_clarification": False,
                "risk_score": 60,
                "risk_class": "medium",
                "irreversible": False,
                "requires_side_effects": True,
                "steps_completed_recent": 1,
                "loop_count": 3,
                "recent_failures": 2,
                "replan_count": 2,
                "grounding_confidence": 0.35,
                "requires_evidence_only": True,
                "tool_success_rate_ewma": 0.4,
                "tool_timeout_count_recent": 0,
                "tool_auth_error_count_recent": 1,
                "llm_calls_used": 3,
                "llm_calls_max": 10,
                "tool_calls_used": 3,
                "tool_calls_max": 8,
                "budget_remaining": 0.5,
                "budget_pressure": 0.5,
            },
        )

    def test_build_meta_metrics_clarification_feedback_snapshot(self) -> None:
        state = _base_state()
        state.unresolved_clarify_items = [
            ClarifyQuestion(type="ambiguous_input", question="Which city?")
        ]
        state.open_questions = []
        budget_caps = BudgetCounters(
            ticks=20,
            tool_calls=8,
            a2a_calls=4,
            tokens=1000,
            time_ms=10000,
        )
        decision = RespondDecision(
            confidence=0.99,
            reason_code="clarify",
            respond_kind="clarify",
            question="Which city?",
        )

        metrics = build_meta_metrics(
            state=state,
            budget_caps=budget_caps,
            decision=decision,
            user_input="You are wrong. Be careful and concise. Emergency stop now.",
            cfg=MetaConfig(),
        )

        self.assertEqual(metrics.unknown_fields_count, 1)
        self.assertTrue(metrics.needs_clarification)
        self.assertEqual(metrics.intent_confidence, 0.55)
        self.assertEqual(metrics.ambiguity_score, 0.7)
        self.assertFalse(metrics.user_corrected_me_recently)
        self.assertFalse(metrics.user_requested_thoroughness)
        self.assertFalse(metrics.user_requested_brevity)
        self.assertTrue(metrics.user_kill_requested)


class EvaluateMetaCharacterizationTests(unittest.TestCase):
    def test_build_meta_metrics_returns_canonical_meta_metrics(self) -> None:
        state = _base_state()
        budget_caps = BudgetCounters(
            ticks=20,
            tool_calls=8,
            a2a_calls=4,
            tokens=1000,
            time_ms=10000,
        )
        metrics = build_meta_metrics(
            state=state,
            budget_caps=budget_caps,
            decision=RespondDecision(
                confidence=0.9,
                reason_code="map-guard",
                respond_kind="answer",
                answer="ok",
            ),
            cfg=MetaConfig(),
        )
        self.assertIsInstance(metrics, CanonicalMetaMetrics)

    def test_evaluate_meta_emits_deterministic_events(self) -> None:
        state = _base_state()
        runner = SimpleNamespace(
            _meta_overrides={},
            options=SimpleNamespace(metactl_enabled=True, metactl_config=MetaConfig()),
            profile=SimpleNamespace(
                budgets=SimpleNamespace(
                    max_ticks_per_user_turn=20,
                    max_tool_calls=8,
                    max_a2a_calls=4,
                    max_total_llm_tokens=1000,
                    max_elapsed_ms=10000,
                )
            ),
            meta_api=None,
            meta_engine=CanonicalMetaRulesEngine(CanonicalMetaConfig()),
        )
        logger = _CaptureLogger()

        result_a = evaluate_meta(
            runner,
            state=state.model_copy(deep=True),
            logger=logger,
            hook="before_act",
            user_input="please proceed",
        )
        result_b = evaluate_meta(
            runner,
            state=state.model_copy(deep=True),
            logger=_CaptureLogger(),
            hook="before_act",
            user_input="please proceed",
        )

        self.assertIsNotNone(result_a)
        assert result_a is not None
        assert result_b is not None
        self.assertEqual(result_a.meta_state, result_b.meta_state)
        self.assertEqual(result_a.reasons, result_b.reasons)
        self.assertIn("checkpoint:before_act", result_a.reasons)

        event_types = [event[0] for event in logger.events]
        self.assertIn("meta.metrics", event_types)
        self.assertIn("meta.directive", event_types)
        self.assertIn("policy.evaluated", event_types)

        metrics_event = next(
            event for event in logger.events if event[0] == "meta.metrics"
        )
        metrics_payload = metrics_event[1]
        self.assertEqual(
            metrics_payload["_telemetry_schema_version"], "meta.metrics.v2"
        )
        self.assertIn("recent_failures", metrics_payload)
        self.assertIn("repeat_error_count", metrics_payload)
        self.assertIn("grounding_confidence", metrics_payload)
        self.assertIn("grounding_score", metrics_payload)

        directive_event = next(
            event for event in logger.events if event[0] == "meta.directive"
        )
        payload = directive_event[1]
        self.assertEqual(payload["_telemetry_schema_version"], "meta.directive.v2")
        self.assertIn("grounding_confidence", payload)
        self.assertIn("grounding_score", payload)
        self.assertIn("budget_remaining", payload)
        self.assertIn("budget_pressure", payload)
        self.assertIn("recent_failures", payload["progress"])
        self.assertIn("repeat_error_count", payload["progress"])
