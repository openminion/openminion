from __future__ import annotations

import unittest

from pydantic import ValidationError

from openminion.modules.brain.config import (
    PlanAutoScaleConfig,
    RunnerOptions,
    RuntimeConfig,
    BrainConfig,
)
from openminion.modules.brain.schemas import (
    AgentBudgets,
    ActDecision,
    BudgetCounters,
    DecisionAdapter,
    RespondDecision,
    MetaDirective,
    MetaMetrics,
    MetaResult,
    MetaState,
    Plan,
    IntentExecutionState,
    OutcomeAttributionConfig,
    SuccessMemoryConfig,
    SuccessMemoryReport,
    SubIntent,
    StepOutputEntry,
    ToolCommand,
    VerificationMode,
    WorkingState,
    build_intent_execution_states,
    build_sub_intent_id,
    sub_intent_descriptions,
    to_structured_sub_intents,
)


class SchemaTests(unittest.TestCase):
    def test_tool_command_defaults_kind_and_idempotency_key(self) -> None:
        command = ToolCommand(
            title="missing key",
            tool_name="echo",
            args={"x": 1},
            success_criteria={"status": "success"},
        )
        self.assertEqual(command.kind, "tool")
        self.assertTrue(bool(command.idempotency_key))

    def test_tool_command_normalizes_sub_intent_ids(self) -> None:
        command = ToolCommand(
            title="echo",
            tool_name="echo",
            args={"msg": "ok"},
            success_criteria={"status": "success"},
            sub_intent_ids=("intent_a", " intent_a ", "intent_b", ""),
        )
        self.assertEqual(command.sub_intent_ids, ["intent_a", "intent_b"])

    def test_tool_command_normalizes_skill_id(self) -> None:
        command = ToolCommand(
            title="echo",
            tool_name="echo",
            args={"msg": "ok"},
            success_criteria={"status": "success"},
            skill_id=" beta ",
        )

        self.assertEqual(command.skill_id, "beta")

    def test_tool_command_backfills_args_from_inputs_when_missing(self) -> None:
        command = ToolCommand(
            title="browser",
            tool_name="browser",
            inputs={"op": "tab.navigate", "url": "https://example.com"},
            success_criteria={"status": "success"},
        )
        self.assertEqual(
            command.args,
            {"op": "tab.navigate", "url": "https://example.com"},
        )

    def test_act_decision_allows_runtime_resolved_execution_target(self) -> None:
        decision = ActDecision(
            confidence=1.0,
            reason_code="test",
            act_profile="general",
        )
        self.assertIsNone(decision.execution_target)

    def test_decision_accepts_sub_intents_when_present(self) -> None:
        decision = RespondDecision(
            confidence=0.9,
            reason_code="with_sub_intents",
            respond_kind="answer",
            answer="ok",
            sub_intents=["start_browser", "navigate_to_url"],
        )
        self.assertEqual(decision.sub_intents, ["start_browser", "navigate_to_url"])

    def test_decision_defaults_sub_intents_when_absent(self) -> None:
        decision = RespondDecision(
            confidence=0.9,
            reason_code="without_sub_intents",
            respond_kind="answer",
            answer="ok",
        )
        self.assertEqual(decision.sub_intents, [])

    def test_decision_rejects_structured_sub_intents_until_cutover(self) -> None:
        with self.assertRaises(ValidationError):
            RespondDecision(
                confidence=0.9,
                reason_code="structured_sub_intents_not_yet_allowed",
                respond_kind="answer",
                answer="ok",
                sub_intents=[
                    {"id": "intent_01_check_weather", "description": "check weather"}
                ],
            )

    def test_decision_act_with_rationale(self) -> None:
        decision = ActDecision(
            confidence=1.0,
            reason_code="act_with_rationale",
            act_profile="general",
            execution_target={"kind": "local"},
            rationale="One command fully satisfies this request.",
        )
        self.assertEqual(
            decision.rationale,
            "One command fully satisfies this request.",
        )

    def test_decision_act_empty_rationale_is_graceful(self) -> None:
        decision = ActDecision(
            confidence=1.0,
            reason_code="act_empty_rationale",
            act_profile="general",
            execution_target={"kind": "local"},
        )
        self.assertEqual(decision.rationale, "")

    def test_structured_sub_intents_from_legacy_labels_have_stable_ids(self) -> None:
        structured = to_structured_sub_intents(["start browser", "navigate to google"])

        self.assertEqual(
            structured,
            [
                SubIntent(
                    id=build_sub_intent_id("start browser", index=1),
                    description="start browser",
                ),
                SubIntent(
                    id=build_sub_intent_id("navigate to google", index=2),
                    description="navigate to google",
                ),
            ],
        )

    def test_structured_sub_intents_preserve_dependency_edges(self) -> None:
        structured = to_structured_sub_intents(
            [
                {"id": "intent_browser", "description": "start browser"},
                {
                    "id": "intent_nav",
                    "description": "navigate to google",
                    "depends_on": ["intent_browser"],
                    "conditional": True,
                },
            ]
        )

        self.assertEqual(structured[1].depends_on, ["intent_browser"])
        self.assertTrue(structured[1].conditional)
        self.assertEqual(
            sub_intent_descriptions(structured),
            ["start browser", "navigate to google"],
        )

    def test_sub_intent_rejects_self_dependency(self) -> None:
        with self.assertRaises(ValidationError):
            SubIntent(
                id="intent_browser",
                description="start browser",
                depends_on=["intent_browser"],
            )

    def test_plan_accepts_structured_sub_intent_refs(self) -> None:
        plan = Plan(
            objective="browse",
            steps=[
                ToolCommand(
                    title="echo",
                    tool_name="echo",
                    args={"msg": "ok"},
                    success_criteria={"status": "success"},
                    sub_intent_ids=["intent_01_start_browser"],
                )
            ],
            stop_conditions=["done"],
            assumptions=[],
            risk_summary="low",
            success_criteria={"status": "success"},
            sub_intents=[
                SubIntent(
                    id="intent_01_start_browser",
                    description="start browser",
                )
            ],
        )
        self.assertEqual(plan.sub_intents[0].id, "intent_01_start_browser")
        self.assertEqual(plan.steps[0].sub_intent_ids, ["intent_01_start_browser"])

    def test_plan_rejects_finish_message_with_unresolved_template_placeholders(
        self,
    ) -> None:
        with self.assertRaises(ValidationError):
            Plan(
                objective="research",
                steps=[
                    {
                        "kind": "finish",
                        "title": "Deliver summary",
                        "final_message": (
                            "Based on the research, here is the answer:\n\n[SUMMARY]"
                        ),
                    }
                ],
                stop_conditions=["done"],
                assumptions=[],
                risk_summary="low",
                success_criteria={},
            )

        with self.assertRaises(ValidationError):
            Plan(
                objective="research",
                steps=[
                    {
                        "kind": "finish",
                        "title": "Deliver summary",
                        "final_message": (
                            "Here is a summary of the latest news: "
                            "{{$defs.ThinkCommand.output_key}}"
                        ),
                    }
                ],
                stop_conditions=["done"],
                assumptions=[],
                risk_summary="low",
                success_criteria={},
            )

    def test_plan_rejects_tool_args_with_unknown_sentinel(self) -> None:
        with self.assertRaises(ValidationError):
            Plan(
                objective="review result",
                steps=[
                    {
                        "kind": "tool",
                        "title": "Review result",
                        "tool_name": "web.fetch",
                        "args": {"url": "<UNKNOWN>"},
                        "success_criteria": {"status": "success"},
                    }
                ],
                stop_conditions=["done"],
                assumptions=[],
                risk_summary="low",
                success_criteria={},
            )

    def test_build_intent_execution_states_preserves_existing_status(self) -> None:
        intent_id = build_sub_intent_id("start browser", index=1)
        states = build_intent_execution_states(
            [{"id": intent_id, "description": "start browser", "skill_id": "browser"}],
            existing=[
                IntentExecutionState(
                    intent_id=intent_id,
                    description="old label",
                    skill_id="legacy",
                    status="retrying",
                    last_command_id="cmd-1",
                )
            ],
        )
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0].intent_id, intent_id)
        self.assertEqual(states[0].description, "start browser")
        self.assertEqual(states[0].skill_id, "browser")
        self.assertEqual(states[0].status, "retrying")
        self.assertEqual(states[0].last_command_id, "cmd-1")

    def test_working_state_derives_intent_execution_states_from_decision_refs(
        self,
    ) -> None:
        intent_id = build_sub_intent_id("check weather", index=1)
        state = WorkingState(
            session_id="s-intent-state",
            agent_id="agent",
            budgets_remaining=BudgetCounters(
                ticks=5,
                tool_calls=3,
                a2a_calls=1,
                tokens=1000,
                time_ms=10000,
            ),
            decision_sub_intents=["check weather"],
            decision_sub_intent_refs=[
                SubIntent(id=intent_id, description="check weather")
            ],
        )
        self.assertEqual(len(state.intent_execution_states), 1)
        self.assertEqual(state.intent_execution_states[0].intent_id, intent_id)
        self.assertEqual(state.intent_execution_states[0].status, "pending")

    def test_working_state_tracks_multi_active_skill_ids(self) -> None:
        state = WorkingState(
            session_id="s-skill-state",
            agent_id="agent",
            budgets_remaining=BudgetCounters(
                ticks=5,
                tool_calls=3,
                a2a_calls=1,
                tokens=1000,
                time_ms=10000,
            ),
            resolved_skill_ids=["alpha", " beta ", "alpha"],
        )

        self.assertEqual(state.active_skill_id, "alpha")
        self.assertEqual(state.active_skill_ids, ["alpha", "beta"])
        self.assertEqual(state.resolved_skill_ids, ["alpha", "beta"])

    def test_success_memory_config_defaults(self) -> None:
        config = SuccessMemoryConfig()

        self.assertFalse(config.enabled)
        self.assertEqual(config.max_items_per_turn, 3)
        self.assertTrue(config.procedure_enabled)
        self.assertTrue(config.tool_habit_enabled)
        self.assertTrue(config.require_closure_satisfied)
        self.assertFalse(config.require_all_steps_successful)
        self.assertEqual(config.min_item_confidence, 0.7)

    def test_success_memory_report_accepts_procedure_and_tool_habit(self) -> None:
        report = SuccessMemoryReport.model_validate(
            {
                "session_id": "sess-1",
                "agent_id": "agent-1",
                "outcome": "success",
                "command_ids": ["cmd-1"],
                "items": [
                    {
                        "kind": "procedure",
                        "title": "Procedure for deploys",
                        "content": {"steps": ["check status", "deploy"]},
                        "confidence": 0.9,
                    },
                    {
                        "kind": "tool_habit",
                        "title": "Use weather before travel",
                        "content": "Check weather before booking",
                        "confidence": 0.8,
                    },
                ],
            }
        )

        self.assertEqual(report.outcome, "success")
        self.assertEqual(
            [item.kind for item in report.items], ["procedure", "tool_habit"]
        )

    def test_decision_mode_description_prefers_shared_act_loop_for_tool_factuals(
        self,
    ) -> None:
        route_schema = DecisionAdapter.json_schema()["properties"]["route"]
        description = str(route_schema.get("description", ""))
        self.assertIn(
            "Do not use 'respond' for tool-eligible factual asks", description
        )
        self.assertIn("execute work now through the shared act loop", description)

    def test_decision_adapter_flat_schema_preserves_flat_compat_shape(self) -> None:
        schema = DecisionAdapter.flat_json_schema()
        properties = schema["properties"]

        self.assertNotIn("oneOf", schema)
        self.assertEqual(properties["route"]["enum"], ["respond", "act"])
        self.assertIn("rationale", properties)
        self.assertIn("question", properties)
        self.assertIn("answer", properties)
        self.assertIn("act_profile", properties)
        self.assertIn("execution_target", properties)

    def test_decision_adapter_validates_all_modes_via_discriminated_union(self) -> None:
        respond = DecisionAdapter.validate_python(
            {
                "mode": "respond",
                "confidence": 0.9,
                "reason_code": "greeting",
                "respond_kind": "answer",
                "answer": "hi",
            }
        )
        act = DecisionAdapter.validate_python(
            {
                "mode": "act",
                "confidence": 0.9,
                "reason_code": "simple_weather_query",
                "act_profile": "general",
                "execution_target": {"kind": "local"},
                "rationale": "One weather tool call is enough.",
            }
        )
        # Compat bridge: old mode="plan" payloads are rewritten to ActDecision
        plan_compat = DecisionAdapter.validate_python(
            {
                "mode": "plan",
                "confidence": 0.8,
                "reason_code": "compound_request",
                "plan_strategy": "sequential",
                "plan_hint": "Need multiple steps.",
            }
        )
        clarify = DecisionAdapter.validate_python(
            {
                "mode": "respond",
                "confidence": 0.7,
                "reason_code": "needs_clarification",
                "respond_kind": "clarify",
                "question": "Which city?",
            }
        )

        self.assertIsInstance(respond, RespondDecision)
        self.assertIsInstance(act, ActDecision)
        self.assertIsInstance(plan_compat, ActDecision)
        self.assertEqual(plan_compat.act_profile, "general")
        self.assertIsInstance(clarify, RespondDecision)
        self.assertEqual(clarify.respond_kind, "clarify")


class MetaSchemaTests(unittest.TestCase):
    def test_meta_metrics_clamps_and_normalizes_fields(self) -> None:
        metrics = MetaMetrics(
            intent_confidence=2.0,
            ambiguity_score=-0.5,
            risk_score=150,
            llm_calls_used=-3,
            llm_calls_max=0,
            budget_pressure=1.5,
        )

        self.assertEqual(metrics.intent_confidence, 1.0)
        self.assertEqual(metrics.ambiguity_score, 0.0)
        self.assertEqual(metrics.risk_score, 100)
        self.assertEqual(metrics.llm_calls_used, 0)
        self.assertEqual(metrics.llm_calls_max, 0)
        self.assertEqual(metrics.budget_pressure, 1.0)

    def test_meta_directive_validates_literal_fields(self) -> None:
        with self.assertRaises(ValidationError):
            MetaDirective(override_next_state="PAUSE")

        directive = MetaDirective(
            override_next_state="WAITING",
            tier_override="T2_tool",
            require_verification=True,
            verification_mode=VerificationMode.rule_based,
            prompt_constraints=["state assumptions"],
        )

        self.assertEqual(directive.override_next_state, "WAITING")
        self.assertIn("state assumptions", directive.prompt_constraints)

    def test_meta_result_requires_valid_components(self) -> None:
        metrics = MetaMetrics(risk_class="medium", risk_score=60)
        directive = MetaDirective(require_verification=True)

        result = MetaResult(
            meta_state=MetaState.CAUTIOUS,
            directive=directive,
            metrics=metrics,
            reasons=["medium risk"],
        )

        self.assertEqual(result.meta_state, MetaState.CAUTIOUS)
        self.assertTrue(result.directive.require_verification)


class ClarificationSchemaTests(unittest.TestCase):
    def test_clarify_question_defaults(self) -> None:
        from openminion.modules.brain.schemas import ClarifyQuestion

        q = ClarifyQuestion(
            type="missing_field",
            question="What is the target environment?",
        )
        self.assertTrue(q.is_blocking)
        self.assertEqual(q.confidence_threshold, 0.5)

    def test_clarify_request_validation(self) -> None:
        from openminion.modules.brain.schemas import (
            ClarifyRequest,
            ClarifyQuestion,
            BrainMode,
            ClarifyPolicy,
        )

        q = ClarifyQuestion(type="ambiguous_input", question="Which file?")
        req = ClarifyRequest(
            session_id="sess_123",
            trace_id="trace_456",
            questions=[q],
            mode=BrainMode.GUIDED,
            policy=ClarifyPolicy.ASK_IF_AMBIGUOUS,
        )

        self.assertEqual(req.session_id, "sess_123")
        self.assertEqual(len(req.questions), 1)
        self.assertEqual(req.mode, "guided")

    def test_brain_mode_and_policy_enums(self) -> None:
        from openminion.modules.brain.schemas import BrainMode, ClarifyPolicy

        self.assertEqual(BrainMode.COMMAND, "command")
        self.assertEqual(ClarifyPolicy.SMART_ASSUME, "smart_assume")


class Phase2SchemaTests(unittest.TestCase):
    def test_working_state_step_outputs_roundtrip(self) -> None:
        state = WorkingState(
            session_id="phase2-step-outputs",
            agent_id="agent",
            budgets_remaining=BudgetCounters(
                ticks=5,
                tool_calls=3,
                a2a_calls=1,
                tokens=1000,
                time_ms=10000,
            ),
            step_outputs=[
                StepOutputEntry(
                    step_index=1,
                    command_id="cmd-1",
                    output_key="flight_search",
                    summary="Found flight candidates",
                    sub_intent_ids=["intent_01_research_flights"],
                    outputs={"count": 3},
                    artifact_refs=["artifact://flight-1"],
                )
            ],
        )

        dumped = state.model_dump(mode="json")
        loaded = WorkingState.model_validate(dumped)
        self.assertEqual(len(loaded.step_outputs), 1)
        self.assertEqual(loaded.step_outputs[0].output_key, "flight_search")
        self.assertEqual(
            loaded.step_outputs[0].sub_intent_ids, ["intent_01_research_flights"]
        )
        self.assertEqual(loaded.step_outputs[0].outputs.get("count"), 3)

    def test_runner_options_plan_auto_scale_defaults_and_clamp(self) -> None:
        defaults = RunnerOptions()
        self.assertEqual(defaults.plan_auto_scale_max_llm_calls, 128)
        self.assertEqual(defaults.plan_auto_scale_max_ticks, 128)
        self.assertEqual(defaults.plan_auto_scale_max_tokens, 500_000)
        self.assertEqual(defaults.plan_auto_scale_max_elapsed_ms, 300_000)
        self.assertEqual(defaults.plan_auto_scale_base_overhead_ms, 20_000)
        self.assertEqual(defaults.plan_auto_scale_per_step_time_ms, 15_000)
        self.assertEqual(defaults.skill_selection_strategy, "llm")
        self.assertTrue(defaults.outcome_attribution_config.enabled)
        self.assertEqual(
            defaults.outcome_attribution_config.max_memory_refs_per_command, 12
        )
        self.assertTrue(defaults.outcome_attribution_config.include_fact_refs)
        self.assertTrue(defaults.outcome_attribution_config.include_procedure_refs)

        clamped = RunnerOptions(
            plan_auto_scale_max_llm_calls=0,
            plan_auto_scale_max_ticks=-9,
            plan_auto_scale_max_tokens=10,
            plan_auto_scale_max_elapsed_ms=1,
            plan_auto_scale_base_overhead_ms=-5,
            plan_auto_scale_per_step_time_ms=0,
        )
        self.assertEqual(clamped.plan_auto_scale_max_llm_calls, 128)
        self.assertEqual(clamped.plan_auto_scale_max_ticks, 1)
        self.assertEqual(clamped.plan_auto_scale_max_tokens, 1000)
        self.assertEqual(clamped.plan_auto_scale_max_elapsed_ms, 1000)
        self.assertEqual(clamped.plan_auto_scale_base_overhead_ms, 0)
        self.assertEqual(clamped.plan_auto_scale_per_step_time_ms, 1)

    def test_runner_options_normalizes_skill_selection_strategy(self) -> None:
        self.assertEqual(
            RunnerOptions(skill_selection_strategy="LLM").skill_selection_strategy,
            "llm",
        )
        with self.assertLogs(
            "openminion.modules.brain.config", level="WARNING"
        ) as captured:
            invalid = RunnerOptions(skill_selection_strategy="unsupported")
        self.assertEqual(invalid.skill_selection_strategy, "llm")
        self.assertTrue(
            any("Invalid skill_selection_strategy" in line for line in captured.output)
        )

    def test_runtime_config_accepts_plan_auto_scale_overrides(self) -> None:
        cfg = RuntimeConfig(
            brain=BrainConfig(
                budgets=AgentBudgets(
                    max_ticks_per_user_turn=4,
                    max_tool_calls=2,
                    max_a2a_calls=0,
                    max_total_llm_tokens=2000,
                    max_elapsed_ms=10000,
                ),
                plan_auto_scale=PlanAutoScaleConfig(
                    max_llm_calls=32,
                    max_ticks=16,
                    max_tokens=62_000,
                    max_elapsed_ms=180_000,
                    base_overhead_ms=12_000,
                    per_step_time_ms=9_000,
                ),
            )
        )
        self.assertEqual(cfg.brain.plan_auto_scale.max_llm_calls, 32)
        self.assertEqual(cfg.brain.plan_auto_scale.max_ticks, 16)
        self.assertEqual(cfg.brain.plan_auto_scale.max_tokens, 62_000)
        self.assertEqual(cfg.brain.plan_auto_scale.max_elapsed_ms, 180_000)
        self.assertEqual(cfg.brain.plan_auto_scale.base_overhead_ms, 12_000)
        self.assertEqual(cfg.brain.plan_auto_scale.per_step_time_ms, 9_000)

    def test_runtime_config_accepts_skill_selection_strategy_override(self) -> None:
        cfg = RuntimeConfig(
            brain=BrainConfig(
                budgets=AgentBudgets(
                    max_ticks_per_user_turn=4,
                    max_tool_calls=2,
                    max_a2a_calls=0,
                    max_total_llm_tokens=2000,
                    max_elapsed_ms=10000,
                ),
                skill_selection_strategy="llm",
            )
        )
        self.assertEqual(cfg.brain.skill_selection_strategy, "llm")

    def test_runtime_config_accepts_context_budget_prerouting_override(self) -> None:
        cfg = RuntimeConfig(
            brain=BrainConfig(
                budgets=AgentBudgets(
                    max_ticks_per_user_turn=4,
                    max_tool_calls=2,
                    max_a2a_calls=0,
                    max_total_llm_tokens=2000,
                    max_elapsed_ms=10000,
                ),
            )
        )
        # `context_budget_prerouting_enabled` removed; this test
        # now only asserts construction succeeds with the override-free
        # shape.
        self.assertEqual(cfg.brain.budgets.max_tool_calls, 2)

    def test_runtime_config_accepts_outcome_attribution_override(self) -> None:
        cfg = RuntimeConfig(
            brain=BrainConfig(
                budgets=AgentBudgets(
                    max_ticks_per_user_turn=4,
                    max_tool_calls=2,
                    max_a2a_calls=0,
                    max_total_llm_tokens=2000,
                    max_elapsed_ms=10000,
                ),
                outcome_attribution=OutcomeAttributionConfig(
                    enabled=False,
                    success_feedback_delta=0.2,
                    failure_feedback_delta=-0.25,
                    timeout_feedback_delta=-0.15,
                    max_memory_refs_per_command=5,
                    include_fact_refs=False,
                    include_procedure_refs=False,
                ),
            )
        )
        self.assertFalse(cfg.brain.outcome_attribution.enabled)
        self.assertEqual(cfg.brain.outcome_attribution.success_feedback_delta, 0.2)
        self.assertEqual(cfg.brain.outcome_attribution.failure_feedback_delta, -0.25)
        self.assertEqual(cfg.brain.outcome_attribution.timeout_feedback_delta, -0.15)
        self.assertEqual(cfg.brain.outcome_attribution.max_memory_refs_per_command, 5)
        self.assertFalse(cfg.brain.outcome_attribution.include_fact_refs)
        self.assertFalse(cfg.brain.outcome_attribution.include_procedure_refs)
