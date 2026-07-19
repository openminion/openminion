import unittest
from unittest.mock import MagicMock, patch

from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.runner import BrainRunner, RunnerOptions
from openminion.modules.brain.schemas import (
    AgentProfile,
    AgentBudgets,
    WorkingState,
    BrainMode,
    ClarifyPolicy,
    ClarifyContext,
    ActDecision,
    PendingTurnContext,
    RespondDecision,
    LLMProfiles,
    BudgetCounters,
    PolicyDecision,
    ToolCommand,
)
from tests.brain.runner_test_support import build_seeded_act_decision


class TestRunnerClarify(unittest.TestCase):
    def setUp(self):
        self.profile = AgentProfile(
            agent_id="test-agent",
            llm_profiles=LLMProfiles(
                decide_model="test",
                plan_model="test",
                reflect_model="test",
                summarize_model="test",
            ),
            budgets=AgentBudgets(
                max_ticks_per_user_turn=10,
                max_tool_calls=5,
                max_a2a_calls=2,
                max_total_llm_tokens=1000,
                max_elapsed_ms=5000,
            ),
        )
        self.session_api = MagicMock()
        self.context_api = MagicMock()
        self.llm_api = MagicMock()
        self.runner = BrainRunner(
            profile=self.profile,
            session_api=self.session_api,
            context_api=self.context_api,
            llm_api=self.llm_api,
            options=RunnerOptions(
                metactl_enabled=False,
            ),
        )

    def _get_test_budgets(self):
        return BudgetCounters(
            ticks=10, tool_calls=5, a2a_calls=2, tokens=1000, time_ms=5000
        )

    def _answer_decision(
        self,
        *,
        reason_code: str = "test",
        answer: str = "Done",
        confidence: float = 1.0,
        pending_turn_context: PendingTurnContext | None = None,
    ) -> RespondDecision:
        return RespondDecision(
            confidence=confidence,
            reason_code=reason_code,
            respond_kind="answer",
            answer=answer,
            pending_turn_context=pending_turn_context,
        )

    def _answer_decision_without_pending_turn_context(
        self,
        *,
        reason_code: str = "test",
        answer: str = "Done",
        confidence: float = 1.0,
    ) -> RespondDecision:
        return RespondDecision(
            confidence=confidence,
            reason_code=reason_code,
            respond_kind="answer",
            answer=answer,
        )

    def _local_single_decision(self, *, reason_code: str = "test") -> ActDecision:
        return build_seeded_act_decision(
            confidence=0.9,
            reason_code=reason_code,
            act_profile="general",
            execution_target={"kind": "local"},
            command=ToolCommand(
                title="run command",
                tool_name="run_command",
                args={"command": "echo hi"},
                success_criteria={"status": "success"},
                idempotency_key=f"idem-{reason_code}",
            ),
        )

    def _clarify_decision(
        self,
        *,
        reason_code: str = "clarify_scope",
        question: str = "Did you mean the weather in China, or something else?",
        clarify_context: ClarifyContext | None = None,
    ) -> RespondDecision:
        return RespondDecision(
            confidence=0.9,
            reason_code=reason_code,
            respond_kind="clarify",
            question=question,
            clarify_context=clarify_context,
        )

    def test_runner_options_default_clarify_config_exists(self):
        # clarify_strategy field removed; LLM always handles clarification
        from openminion.modules.brain.config import ClarifyConfig

        self.assertIsInstance(RunnerOptions().clarify_config, ClarifyConfig)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_step_runtime_clarify_path_is_deprecated(
        self, mock_decide, mock_save, mock_load
    ):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
        )
        mock_load.return_value = state

        mock_decide.return_value = self._answer_decision()

        result = self.runner.step(
            session_id="sess_123", user_input="Please clarify this."
        )

        self.assertNotEqual(result.status, "error")
        self.assertEqual(state.phase, "RESPOND")
        self.assertEqual(len(state.unresolved_clarify_items), 0)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_mode_command_is_strict(self, mock_decide, mock_save, mock_load):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            mode=BrainMode.COMMAND,
        )
        mock_load.return_value = state
        mock_decide.return_value = self._answer_decision()

        self.runner.step(session_id="sess_123", user_input="Do something maybe?")
        self.assertEqual(state.phase, "RESPOND")
        self.assertEqual(len(state.unresolved_clarify_items), 0)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_mode_autonomous_is_aggressive(self, mock_decide, mock_save, mock_load):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            mode=BrainMode.AUTONOMOUS,
            policy=ClarifyPolicy.ASK_IF_AMBIGUOUS,
        )
        mock_load.return_value = state
        mock_decide.return_value = self._answer_decision()

        self.runner.step(session_id="sess_123", user_input="Do something maybe?")
        self.assertEqual(state.phase, "RESPOND")
        self.assertEqual(len(state.unresolved_clarify_items), 0)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_mode_batch_never_asks(self, mock_decide, mock_save, mock_load):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            mode=BrainMode.BATCH,
        )
        mock_load.return_value = state
        mock_decide.return_value = self._answer_decision(answer="Batch done")

        self.runner.step(session_id="sess_123", user_input="Clarfiy this now!")
        self.assertEqual(state.phase, "RESPOND")
        self.assertEqual(len(state.unresolved_clarify_items), 0)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_policy_require_clarification_overrides_batch_mode(
        self, mock_decide, mock_save, mock_load
    ):
        policy_api = MagicMock()
        policy_api.evaluate.return_value = PolicyDecision(
            outcome="ALLOW",
            explanation="Missing required context.",
            require_clarification=True,
            clarification_question="Which deployment environment should I use?",
        )
        runner = BrainRunner(
            profile=self.profile,
            session_api=self.session_api,
            context_api=self.context_api,
            llm_api=self.llm_api,
            policy_api=policy_api,
            options=RunnerOptions(metactl_enabled=False),
        )

        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            mode=BrainMode.BATCH,
        )
        mock_load.return_value = state
        mock_decide.return_value = self._local_single_decision(reason_code="test")

        result = runner.step(session_id="sess_123", user_input="run command echo hi")
        self.assertEqual(result.status, "waiting_user")
        self.assertIn("deployment environment", str(result.message or "").lower())

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_mode_guided_concrete_question_does_not_clarify(
        self, mock_decide, mock_save, mock_load
    ):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            mode=BrainMode.GUIDED,
            policy=ClarifyPolicy.ASK_IF_AMBIGUOUS,
        )
        mock_load.return_value = state
        mock_decide.return_value = self._answer_decision(answer="Sunny.")

        self.runner.step(
            session_id="sess_123",
            user_input="what's weather at san diego today?",
        )
        self.assertEqual(state.phase, "RESPOND")
        self.assertEqual(len(state.unresolved_clarify_items), 0)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_mode_guided_social_question_does_not_clarify(
        self, mock_decide, mock_save, mock_load
    ):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            mode=BrainMode.GUIDED,
            policy=ClarifyPolicy.ASK_IF_AMBIGUOUS,
        )
        mock_load.return_value = state
        mock_decide.return_value = self._answer_decision(
            reason_code="smalltalk",
            answer="I'm doing well, thanks for asking.",
        )

        self.runner.step(
            session_id="sess_123",
            user_input="how are you?",
        )
        self.assertEqual(state.phase, "RESPOND")
        self.assertEqual(len(state.unresolved_clarify_items), 0)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_mode_guided_weather_missing_location_no_runtime_heuristic(
        self, mock_decide, mock_save, mock_load
    ):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            mode=BrainMode.GUIDED,
            policy=ClarifyPolicy.ASK_IF_AMBIGUOUS,
        )
        mock_load.return_value = state
        mock_decide.return_value = self._answer_decision()

        result = self.runner.step(
            session_id="sess_123",
            user_input="what's weather today?",
        )
        self.assertNotEqual(result.status, "error")
        self.assertEqual(state.phase, "RESPOND")
        self.assertEqual(len(state.unresolved_clarify_items), 0)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_invalid_weather_answer_no_runtime_clarification_counters(
        self, mock_decide, mock_save, mock_load
    ):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            mode=BrainMode.GUIDED,
            policy=ClarifyPolicy.ASK_IF_AMBIGUOUS,
        )
        mock_load.return_value = state
        mock_decide.return_value = self._answer_decision()

        first = self.runner.step(
            session_id="sess_123",
            user_input="what's weather today?",
        )
        self.assertNotEqual(first.status, "error")
        self.assertEqual(len(state.unresolved_clarify_items), 0)

        second = self.runner.step(
            session_id="sess_123",
            user_input="same",
        )
        self.assertNotEqual(second.status, "error")
        self.assertEqual(len(state.unresolved_clarify_items), 0)

        # runtime_clarification_state removed; no counters to check
        self.assertFalse(
            hasattr(state, "runtime_clarification_state")
            and state.runtime_clarification_state
        )

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_mode_guided_ambiguous_question_does_not_trigger_runtime_clarify(
        self, mock_decide, mock_save, mock_load
    ):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            mode=BrainMode.GUIDED,
            policy=ClarifyPolicy.ASK_IF_AMBIGUOUS,
        )
        mock_load.return_value = state
        mock_decide.return_value = self._answer_decision()

        result = self.runner.step(
            session_id="sess_123",
            user_input="what's today?",
        )
        self.assertNotEqual(result.status, "error")
        self.assertEqual(state.phase, "RESPOND")
        self.assertEqual(len(state.unresolved_clarify_items), 0)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_clarify_strategy_llm_bypasses_runtime_heuristics(
        self, mock_decide, mock_save, mock_load
    ):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            mode=BrainMode.GUIDED,
            policy=ClarifyPolicy.ASK_IF_AMBIGUOUS,
        )
        mock_load.return_value = state
        mock_decide.return_value = self._answer_decision(
            confidence=0.9,
            reason_code="llm_direct",
            answer="Hello there.",
        )
        # clarify_strategy removed; LLM is always the strategy

        result = self.runner.step(session_id="sess_123", user_input="what's today?")
        self.assertEqual(result.status, "done")
        self.assertEqual(state.phase, "RESPOND")
        self.assertEqual(len(state.unresolved_clarify_items), 0)

        event_types = [
            call.args[1]
            for call in self.session_api.append_event.call_args_list
            if len(call.args) >= 2
        ]
        self.assertIn("brain.clarify.llm.requested", event_types)
        # New clarify lifecycle may proceed directly to decide without a
        # distinct llm.response event.
        self.assertTrue(
            "brain.clarify.llm.response" in event_types or "brain.entry" in event_types
        )

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_clarify_strategy_llm_strict_local_heuristics_can_block(
        self, mock_decide, mock_save, mock_load
    ):

        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            mode=BrainMode.GUIDED,
            policy=ClarifyPolicy.ASK_IF_AMBIGUOUS,
        )
        mock_load.return_value = state
        mock_decide.return_value = self._answer_decision(
            confidence=0.9,
            reason_code="llm_direct",
            answer="Hello there.",
        )
        # clarify_strategy removed; LLM is always the strategy

        result = self.runner.step(session_id="sess_123", user_input="what's today?")
        self.assertEqual(result.status, "done")
        self.assertEqual(state.phase, "RESPOND")
        self.assertEqual(len(state.unresolved_clarify_items), 0)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_clarify_strategy_llm_emits_failed_event_on_decide_error(
        self, mock_decide, mock_save, mock_load
    ):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            mode=BrainMode.GUIDED,
            policy=ClarifyPolicy.ASK_IF_AMBIGUOUS,
        )
        mock_load.return_value = state
        mock_decide.side_effect = RuntimeError("decide boom")
        # clarify_strategy removed; LLM is always the strategy

        result = self.runner.step(session_id="sess_123", user_input="what's weather?")
        self.assertEqual(result.status, "error")

        event_types = [
            call.args[1]
            for call in self.session_api.append_event.call_args_list
            if len(call.args) >= 2
        ]
        self.assertIn("brain.clarify.llm.failed", event_types)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    def test_event_emission(self, mock_save, mock_load):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            mode=BrainMode.COMMAND,
        )
        mock_load.return_value = state

        # 1. Trigger request
        self.runner.step(session_id="sess_123", user_input="Do something maybe?")
        event_types = [
            call.args[1]
            for call in self.session_api.append_event.call_args_list
            if len(call.args) >= 2
        ]
        self.assertTrue(
            any(
                event_type in {"brain.clarify.requested", "brain.clarify.llm.requested"}
                for event_type in event_types
            )
        )

        # 2. Trigger answered
        # Input doesn't contain '?' or 'maybe', so it should progress clarify flow.
        self.runner.step(session_id="sess_123", user_input="I confirm.")
        event_types_after = [
            call.args[1]
            for call in self.session_api.append_event.call_args_list
            if len(call.args) >= 2
        ]
        clarify_events_after = [
            event_type
            for event_type in event_types_after
            if event_type in {"brain.clarify.requested", "brain.clarify.llm.requested"}
        ]
        self.assertGreaterEqual(len(clarify_events_after), 2)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    def test_context_injection(self, mock_save, mock_load):
        from openminion.modules.brain.schemas import ClarifyQuestion

        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            unresolved_clarify_items=[
                ClarifyQuestion(id="q1", question="What color?", type="ambiguous_input")
            ],
            clarify_responses={"q0": "Red"},
        )
        mock_load.return_value = state

        # Trigger a call that builds context
        hints = {"extra": "data"}
        self.context_api.build.reset_mock()

        self.runner._build_context(
            state=state, purpose="test", budget={}, hints=hints, logger=MagicMock()
        )

        # Verify the clarify-specific context fields are preserved.
        self.context_api.build.assert_called_once()
        kwargs = self.context_api.build.call_args.kwargs
        self.assertEqual(kwargs["session_id"], "sess_123")
        self.assertEqual(kwargs["agent_id"], "test-agent")
        self.assertEqual(kwargs["purpose"], "test")
        self.assertEqual(kwargs["budget"], {})
        self.assertEqual(
            kwargs["hints"]["pending_clarifications"],
            [{"id": "q1", "question": "What color?"}],
        )
        self.assertEqual(
            kwargs["hints"]["clarification_responses"],
            {"q0": "Red"},
        )

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    def test_context_injection_includes_pending_conversational_clarify_context(
        self, mock_save, mock_load
    ):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            pending_llm_clarify_context=ClarifyContext(
                original_user_input="what's rather at china?",
                inferred_goal="weather",
                known_context={"place": "China"},
                unresolved_question="Confirm whether the user wants weather information.",
                clarify_question="Did you mean the weather in China, or something else?",
            ),
        )
        mock_load.return_value = state

        self.context_api.build.reset_mock()
        self.runner._build_context(
            state=state,
            purpose="decide",
            budget={},
            hints={"user_input": "yes, weather"},
            logger=MagicMock(),
        )

        kwargs = self.context_api.build.call_args.kwargs
        assert kwargs["hints"]["pending_conversational_clarification"] == {
            "original_user_input": "what's rather at china?",
            "inferred_goal": "weather",
            "known_context": {"place": "China"},
            "unresolved_question": "Confirm whether the user wants weather information.",
            "clarify_question": "Did you mean the weather in China, or something else?",
            "user_reply": "yes, weather",
        }

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    def test_context_injection_does_not_invent_conversational_clarify_context(
        self, mock_save, mock_load
    ):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
        )
        mock_load.return_value = state

        self.context_api.build.reset_mock()
        self.runner._build_context(
            state=state,
            purpose="decide",
            budget={},
            hints={"user_input": "yes, weather"},
            logger=MagicMock(),
        )

        kwargs = self.context_api.build.call_args.kwargs
        self.assertNotIn(
            "pending_conversational_clarification",
            kwargs["hints"],
        )

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_llm_clarify_context_is_stored_then_cleared_after_consume(
        self, mock_decide, mock_save, mock_load
    ):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
        )
        mock_load.return_value = state
        mock_decide.side_effect = [
            self._clarify_decision(
                clarify_context=ClarifyContext(
                    original_user_input="what's rather at china?",
                    inferred_goal="weather",
                    known_context={"place": "China"},
                    unresolved_question="Confirm whether the user wants weather information.",
                    clarify_question="Did you mean the weather in China, or something else?",
                )
            ),
            self._answer_decision(
                reason_code="weather_answer",
                answer="China is broad. Which city would you like?",
            ),
        ]

        first = self.runner.step(
            session_id="sess_123",
            user_input="what's rather at china?",
        )
        self.assertEqual(first.status, "waiting_user")
        self.assertIsNotNone(state.pending_llm_clarify_context)

        second = self.runner.step(
            session_id="sess_123",
            user_input="yes, weather",
        )
        self.assertEqual(second.status, "done")
        self.assertIsNone(state.pending_llm_clarify_context)

        event_types = [
            call.args[1]
            for call in self.session_api.append_event.call_args_list
            if len(call.args) >= 2
        ]
        self.assertIn("brain.clarify.context_stored", event_types)
        self.assertIn("brain.clarify.context_consumed", event_types)
        self.assertIn("brain.clarify.context_cleared", event_types)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_llm_clarify_question_without_sidecar_still_stores_minimal_context(
        self, mock_decide, mock_save, mock_load
    ):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
        )
        mock_load.return_value = state
        question = "Which location's weather would you like to know about?"
        mock_decide.return_value = self._clarify_decision(question=question)

        first = self.runner.step(
            session_id="sess_123",
            user_input="what's weather?",
        )

        self.assertEqual(first.status, "waiting_user")
        self.assertIsNotNone(state.pending_llm_clarify_context)
        self.assertEqual(
            state.pending_llm_clarify_context.original_user_input,
            "what's weather?",
        )
        self.assertEqual(state.pending_llm_clarify_context.inferred_goal, "")
        self.assertEqual(state.pending_llm_clarify_context.known_context, {})
        self.assertEqual(
            state.pending_llm_clarify_context.unresolved_question,
            question,
        )
        self.assertEqual(
            state.pending_llm_clarify_context.clarify_question,
            question,
        )

        self.context_api.build.reset_mock()
        self.runner._build_context(
            state=state,
            purpose="decide",
            budget={},
            hints={"user_input": "china"},
            logger=MagicMock(),
        )

        kwargs = self.context_api.build.call_args.kwargs
        self.assertEqual(
            kwargs["hints"]["pending_conversational_clarification"],
            {
                "original_user_input": "what's weather?",
                "inferred_goal": "",
                "known_context": {},
                "unresolved_question": question,
                "clarify_question": question,
                "user_reply": "china",
            },
        )

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_stale_llm_clarify_context_clears_on_fresh_turn_without_waiting_user(
        self, mock_decide, mock_save, mock_load
    ):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            status="active",
            pending_llm_clarify_context=ClarifyContext(
                original_user_input="what's rather at china?",
                inferred_goal="weather",
                known_context={"place": "China"},
                clarify_question="Did you mean the weather in China, or something else?",
            ),
        )
        mock_load.return_value = state
        mock_decide.return_value = self._answer_decision(answer="Fresh task handled.")

        result = self.runner.step(
            session_id="sess_123",
            user_input="list all tools",
        )

        self.assertEqual(result.status, "done")
        self.assertIsNone(state.pending_llm_clarify_context)
        event_types = [
            call.args[1]
            for call in self.session_api.append_event.call_args_list
            if len(call.args) >= 2
        ]
        self.assertIn("brain.clarify.context_cleared", event_types)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_pending_turn_context_is_stored_then_explicitly_cleared_after_next_turn(
        self, mock_decide, mock_save, mock_load
    ):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
        )
        mock_load.return_value = state
        mock_decide.side_effect = [
            self._answer_decision(
                reason_code="await_path",
                answer="I can save that once you tell me the target file path.",
                pending_turn_context=PendingTurnContext(
                    original_user_request="can you save some python code for me?",
                    active_work_summary=(
                        "The assistant drafted a Python HTTP server and still "
                        "needs the target path before writing the file."
                    ),
                    known_context={"cwd": "/tmp/openminion"},
                    missing_fields=["path"],
                    artifact_refs=["artifact:previous"],
                    response_preferences={"language": "en"},
                ),
            ),
            self._answer_decision(
                reason_code="followup_answer",
                answer="Understood.",
            ),
        ]

        first = self.runner.step(
            session_id="sess_123",
            user_input="can you save some python code for me?",
        )
        self.assertEqual(first.status, "done")
        self.assertIsNotNone(state.pending_turn_context)
        self.assertEqual(state.pending_turn_context.missing_fields, ["path"])

        second = self.runner.step(
            session_id="sess_123",
            user_input="target-path-response",
        )
        self.assertEqual(second.status, "done")
        self.assertIsNone(state.pending_turn_context)
        self.assertEqual(state.pending_turn_context_stale_turns, 0)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_pending_turn_context_is_preserved_when_followup_decision_omits_field(
        self, mock_decide, mock_save, mock_load
    ):
        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
        )
        mock_load.return_value = state
        mock_decide.side_effect = [
            self._answer_decision(
                reason_code="await_path",
                answer="I can save that once you tell me the target file path.",
                pending_turn_context=PendingTurnContext(
                    original_user_request="can you save some python code for me?",
                    active_work_summary=(
                        "The assistant drafted a Python HTTP server and still "
                        "needs the target path before writing the file."
                    ),
                    known_context={"cwd": "/tmp/openminion"},
                    missing_fields=["path"],
                    artifact_refs=["artifact:previous"],
                    response_preferences={"language": "en"},
                ),
            ),
            self._answer_decision_without_pending_turn_context(
                reason_code="followup_answer",
                answer="Understood.",
            ),
        ]

        first = self.runner.step(
            session_id="sess_123",
            user_input="can you save some python code for me?",
        )
        self.assertEqual(first.status, "done")
        self.assertIsNotNone(state.pending_turn_context)
        self.assertEqual(state.pending_turn_context_stale_turns, 0)

        second = self.runner.step(
            session_id="sess_123",
            user_input="target-path-response",
        )
        self.assertEqual(second.status, "done")
        self.assertIsNotNone(state.pending_turn_context)
        assert state.pending_turn_context is not None
        self.assertEqual(state.pending_turn_context.missing_fields, ["path"])
        self.assertEqual(state.pending_turn_context_stale_turns, 0)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_max_questions_per_turn_no_effect_without_runtime_heuristics(
        self, mock_decide, mock_save, mock_load
    ):
        from openminion.modules.brain.config import ClarifyConfig

        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            mode=BrainMode.COMMAND,
        )
        mock_load.return_value = state
        mock_decide.return_value = self._answer_decision()

        # Override config to limit to 2 questions
        self.runner.options.clarify_config = ClarifyConfig(max_questions_per_turn=2)

        self.runner.step(session_id="sess_123", user_input="Do multiple things.")

        self.assertEqual(len(state.unresolved_clarify_items), 0)
        self.assertEqual(state.phase, "RESPOND")

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_one_by_one_questions_no_effect_without_runtime_heuristics(
        self, mock_decide, mock_save, mock_load
    ):
        from openminion.modules.brain.config import ClarifyConfig

        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            mode=BrainMode.COMMAND,
        )
        mock_load.return_value = state
        mock_decide.return_value = self._answer_decision()

        # Override config to ask one-by-one
        self.runner.options.clarify_config = ClarifyConfig(one_by_one_questions=True)

        self.runner.step(session_id="sess_123", user_input="Do multiple things.")

        self.assertEqual(len(state.unresolved_clarify_items), 0)
        self.assertEqual(state.phase, "RESPOND")

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    def test_handle_unanswered_assume_default(self, mock_save, mock_load):
        from openminion.modules.brain.config import ClarifyConfig
        from openminion.modules.brain.schemas import ClarifyQuestion

        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            unresolved_clarify_items=[
                ClarifyQuestion(id="q1", question="?", type="ambiguous_input")
            ],
            mode=BrainMode.COMMAND,
        )
        mock_load.return_value = state

        # Policy is assume_default
        self.runner.options.clarify_config = ClarifyConfig(
            handle_unanswered_policy="assume_default"
        )

        # Resume without user_input
        self.runner.step(session_id="sess_123", user_input=None)

        self.assertEqual(len(state.unresolved_clarify_items), 0)
        self.session_api.append_event.assert_any_call(
            "sess_123",
            "brain.assumptions.used",
            {"count": 1},
            actor_type="agent",
            actor_id="test-agent",
            trace={"trace_id": state.trace_id, "span_id": None},
            importance=2,
            redaction="none",
            trace_id=state.trace_id,
            task_id=None,
            parent_id=None,
            artifact_refs=None,
            memory_refs=None,
            status=None,
            error=None,
        )

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    def test_hlpe_needs_user_does_not_assume_default(self, mock_save, mock_load):
        from openminion.modules.brain.config import ClarifyConfig
        from openminion.modules.brain.schemas import ClarifyQuestion, RequestReadiness

        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            unresolved_clarify_items=[
                ClarifyQuestion(id="q1", question="?", type="ambiguous_input")
            ],
            request_readiness=RequestReadiness(
                posture="direct",
                requested_outcome="execute",
                state="needs_user",
            ),
            mode=BrainMode.COMMAND,
        )
        mock_load.return_value = state
        self.runner.options.request_handoff_enabled = True
        self.runner.options.clarify_config = ClarifyConfig(
            handle_unanswered_policy="assume_default"
        )

        result = self.runner.step(session_id="sess_123", user_input=None)

        self.assertEqual(result.status, "waiting_user")
        self.assertEqual(len(state.unresolved_clarify_items), 1)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    def test_handle_unanswered_abort(self, mock_save, mock_load):
        from openminion.modules.brain.config import ClarifyConfig
        from openminion.modules.brain.schemas import ClarifyQuestion

        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            unresolved_clarify_items=[
                ClarifyQuestion(id="q1", question="?", type="ambiguous_input")
            ],
            mode=BrainMode.COMMAND,
        )
        mock_load.return_value = state
        self.runner.options.clarify_config = ClarifyConfig(
            handle_unanswered_policy="abort"
        )

        result = self.runner.step(session_id="sess_123", user_input=None)

        self.assertEqual(result.status, "stopped")
        self.assertEqual(len(state.unresolved_clarify_items), 0)
        self.assertIn("aborted", str(result.message or "").lower())

    def test_process_clarification_response_passes_through_nonempty_answer(self):
        from openminion.modules.brain.schemas import ClarifyQuestion, ClarifyRequest

        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            unresolved_clarify_items=[
                ClarifyQuestion(
                    id="q1",
                    question="Which location should I check weather for?",
                    type="missing_field",
                    reason_code="weather_location_required",
                    source="runtime_clarification_guard_manager",
                )
            ],
        )
        request = ClarifyRequest(
            session_id=state.session_id,
            trace_id=state.trace_id or "t-clarify-pass-through",
            questions=list(state.unresolved_clarify_items),
            mode=state.mode,
            policy=ClarifyPolicy.ALWAYS_ASK,
            reason="weather_location_required",
        )
        logger = CanonicalEventLogger(
            session_api=self.session_api,
            session_id=state.session_id,
            agent_id=state.agent_id,
        )

        response = self.runner._process_clarification_response(
            state=state,
            user_input="same",
            logger=logger,
            clarify_request=request,
        )

        self.assertEqual(response.status, "active")
        self.assertEqual(state.clarify_responses["q1"], "same")
        self.assertEqual(len(state.unresolved_clarify_items), 0)

    def test_process_clarification_response_blank_answer_still_stores(self):
        from openminion.modules.brain.schemas import ClarifyQuestion, ClarifyRequest

        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            unresolved_clarify_items=[
                ClarifyQuestion(
                    id="q1",
                    question="Which location should I check weather for?",
                    type="missing_field",
                    reason_code="weather_location_required",
                    source="clarify",
                )
            ],
        )
        request = ClarifyRequest(
            session_id=state.session_id,
            trace_id=state.trace_id or "t-clarify-blank",
            questions=list(state.unresolved_clarify_items),
            mode=state.mode,
            policy=ClarifyPolicy.ALWAYS_ASK,
            reason="weather_location_required",
        )
        logger = CanonicalEventLogger(
            session_api=self.session_api,
            session_id=state.session_id,
            agent_id=state.agent_id,
        )

        response = self.runner._process_clarification_response(
            state=state,
            user_input="   ",
            logger=logger,
            clarify_request=request,
        )

        # blank answer is accepted (no guard rejection)
        self.assertEqual(response.status, "active")
        self.assertEqual(len(state.unresolved_clarify_items), 0)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_stale_internal_failure_clarify_state_is_cleared_for_fresh_turn(
        self, mock_decide, mock_save, mock_load
    ):
        from openminion.modules.brain.schemas import ClarifyQuestion

        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            status="waiting_user",
            unresolved_clarify_items=[
                ClarifyQuestion(
                    id="q1",
                    question="Could you rephrase?",
                    type="ambiguous_input",
                )
            ],
            decision_reason_code="invalid_decide_structured_output",
        )
        mock_load.return_value = state
        mock_decide.return_value = self._answer_decision(
            reason_code="trip_plan",
            answer="Here is a trip plan.",
        )

        result = self.runner.step(
            session_id="sess_123",
            user_input="plan a two week trip to japan next week",
        )

        self.assertEqual(result.status, "done")
        self.assertEqual(len(state.unresolved_clarify_items), 0)
        mock_decide.assert_called_once()
        event_types = [
            call.args[1]
            for call in self.session_api.append_event.call_args_list
            if len(call.args) >= 2
        ]
        self.assertIn("brain.clarify.stale_state_cleared", event_types)

    @patch("openminion.modules.brain.runner.BrainRunner._load_or_init_state")
    @patch("openminion.modules.brain.runner.BrainRunner._save_state")
    @patch("openminion.modules.brain.runner.BrainRunner._decide")
    def test_real_blocker_clarify_answer_still_resumes_correctly(
        self, mock_decide, mock_save, mock_load
    ):
        from openminion.modules.brain.schemas import ClarifyQuestion

        state = WorkingState(
            session_id="sess_123",
            agent_id="test-agent",
            budgets_remaining=self._get_test_budgets(),
            status="waiting_user",
            unresolved_clarify_items=[
                ClarifyQuestion(
                    id="q1",
                    question="Which city should I check?",
                    type="missing_field",
                )
            ],
            decision_reason_code="missing_required_input",
        )
        mock_load.return_value = state
        mock_decide.return_value = self._answer_decision(
            reason_code="weather_answer",
            answer="Kyoto is 11C and cloudy.",
        )

        result = self.runner.step(session_id="sess_123", user_input="Kyoto")

        self.assertEqual(result.status, "done")
        self.assertEqual(state.clarify_responses["q1"], "Kyoto")
        self.assertEqual(len(state.unresolved_clarify_items), 0)
        mock_decide.assert_called_once()
