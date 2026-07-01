from types import SimpleNamespace

from openminion.modules.brain.adapters.llm import LocalLLMAdapter

from tests.brain.runner_test_support import (
    BudgetCounters,
    LocalA2AAdapter,
    LocalContextAdapter,
    LocalMemoryAdapter,
    LocalPolicyAdapter,
    LocalSessionStore,
    LocalToolAdapter,
    MagicMock,
    Path,
    Plan,
    RespondDecision,
    RunnerOptions,
    BrainRunner,
    ToolCommand,
    WorkingState,
    _profile,
    tempfile,
    unittest,
)


class RunnerTests(unittest.TestCase):
    def test_load_or_init_state_normalizes_legacy_strict_clarify_mode(self) -> None:
        session_api = MagicMock()
        seed_state = WorkingState(
            session_id="s-legacy",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
            mode="command",
            policy="always_ask",
            unresolved_clarify_items=[
                {
                    "type": "ambiguous_input",
                    "question": 'Clarification needed for: "hello?"',
                    "is_blocking": True,
                }
            ],
        ).model_dump(mode="json")
        session_api.get_latest_working_state.return_value = seed_state
        runner = BrainRunner(profile=_profile(), session_api=session_api)

        state = runner._load_or_init_state("s-legacy")

        self.assertEqual(state.mode, "guided")
        self.assertEqual(state.policy, "ask_if_ambiguous")
        self.assertEqual(state.unresolved_clarify_items, [])
        session_api.put_working_state.assert_called_once()


    def test_pending_session_action_policy_metadata_applies_to_new_state(self) -> None:
        class _SessionApi:
            store = None

            def __init__(self) -> None:
                self.saved = None

            def get_latest_working_state(self, _session_id: str):
                return None

            def put_working_state(self, _session_id: str, *, state_inline):
                self.saved = dict(state_inline)

        session_api = _SessionApi()
        runner = BrainRunner(profile=_profile(), session_api=session_api)
        runner._pending_session_action_policy_mode_override = "auto"

        state = runner._load_or_init_state("s-policy")

        self.assertEqual(state.session_action_policy_mode_override, "auto")
        self.assertEqual(
            session_api.saved["session_action_policy_mode_override"],
            "auto",
        )

    def test_interpret_updates_goal_for_fresh_input(self) -> None:
        runner = BrainRunner(profile=_profile(), session_api=MagicMock())
        state = WorkingState(
            session_id="s-interpret",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
            goal="stale goal",
            open_questions=["stale question?"],
            status="active",
        )

        runner._interpret(state=state, user_input="new request", logger=MagicMock())
        self.assertEqual(state.goal, "new request")
        self.assertEqual(state.open_questions, [])

    def test_direct_response_prefers_decision_answer(self) -> None:
        runner = BrainRunner(profile=_profile(), session_api=MagicMock())
        decision = RespondDecision(
            confidence=0.9,
            reason_code="inventory",
            answer="Tool inventory",
            respond_kind="answer",
        )
        self.assertEqual(
            runner._direct_response(user_input="list all tools", decision=decision),
            "Tool inventory",
        )

    def test_direct_response_uses_conversational_fallback_for_empty_respond_answer(
        self,
    ) -> None:
        runner = BrainRunner(profile=_profile(), session_api=MagicMock())
        decision = SimpleNamespace(
            confidence=0.9,
            reason_code="greeting",
            answer="",
            question="",
            respond_kind="answer",
        )

        self.assertEqual(
            runner._direct_response(user_input="hi", decision=decision),
            "I'm here. What can I help you with?",
        )

    def test_step_respond_decision_completes_done_not_waiting_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=LocalLLMAdapter(),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(metactl_enabled=False),
            )

            decision = RespondDecision(
                confidence=0.95,
                reason_code="greeting",
                answer="Hi there! How can I help you today?",
                respond_kind="answer",
            )
            runner._decide = MagicMock(return_value=decision)  # type: ignore[method-assign]

            output = runner.step(
                session_id="s-respond-done",
                user_input="hi",
                trace_id="trace-respond-done",
            )

            self.assertEqual(output.status, "done")
            latest_state = session.get_latest_working_state("s-respond-done")
            self.assertIsNotNone(latest_state)
            state_inline = latest_state.get("state_inline", {})
            self.assertEqual(state_inline.get("status"), "done")

    def test_update_session_summary_reads_text_fields(self) -> None:
        session_api = MagicMock()
        session_api.list_turns.return_value = [
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "hi there"},
            {"role": "system", "text": "ignore"},
        ]
        runner = BrainRunner(profile=_profile(), session_api=session_api)

        runner._update_session_summary(session_id="s-summary", agent_id="router-agent")

        session_api.update_summary.assert_called_once()
        kwargs = session_api.update_summary.call_args.kwargs
        self.assertEqual(kwargs["session_id"], "s-summary")
        self.assertIn("user: hello", kwargs["summary_short"])
        self.assertIn("assistant: hi there", kwargs["summary_long"])

    def test_tool_echo_run_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=LocalLLMAdapter(),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(reflection_enabled=True),
            )
            output = runner.run(
                session_id="s1",
                user_input='tool echo {"msg":"hello"}',
                trace_id="trace-1",
            )
            self.assertEqual(output.status, "done")
            self.assertIsNotNone(output.action_result)
            assert output.action_result is not None
            self.assertEqual(output.action_result.status, "success")
            self.assertTrue(session.list_events("s1"))

    def test_run_hydrates_goal_runtime_once_per_session(self) -> None:
        runner = BrainRunner(profile=_profile(), session_api=MagicMock())

        class _FakeGoalRuntime:
            def __init__(self) -> None:
                self.calls: list[tuple[str, object]] = []

            def hydrate_session_start(
                self, *, session_id: str, session_api: object
            ) -> None:
                self.calls.append((session_id, session_api))

        goal_runtime = _FakeGoalRuntime()
        runner.goal_runtime = goal_runtime

        output = SimpleNamespace(
            status="done",
            response="ok",
            working_state=WorkingState(
                session_id="sess-lgmh",
                agent_id="router-agent",
                budgets_remaining=BudgetCounters(
                    ticks=10,
                    tool_calls=5,
                    a2a_calls=5,
                    tokens=1000,
                    time_ms=10000,
                ),
            ),
        )

        with unittest.mock.patch(
            "openminion.modules.brain.runner.coordinator.run_until_idle_runner_lifecycle",
            return_value=output,
        ):
            runner.run(session_id="sess-lgmh", user_input="hello", trace_id="trace-1")
            runner.run(session_id="sess-lgmh", user_input="again", trace_id="trace-2")

        self.assertEqual(goal_runtime.calls, [("sess-lgmh", runner.session_api)])

    def test_idempotency_cache_reuses_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(
                    reflection_enabled=False,
                    idempotency_enabled=True,
                    idempotency_cache_size=32,
                ),
            )

            first = runner.step(
                session_id="s2", user_input='tool echo {"x":1}', trace_id="trace-1"
            )
            tool_calls_after_first = first.working_state.budgets_remaining.tool_calls

            second = runner.step(
                session_id="s2", user_input='tool echo {"x":1}', trace_id="trace-2"
            )
            tool_calls_after_second = second.working_state.budgets_remaining.tool_calls

            self.assertEqual(tool_calls_after_first, tool_calls_after_second)

    def test_high_risk_command_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(reflection_enabled=False),
            )

            command = ToolCommand(
                title="dangerous op",
                tool_name="echo",
                args={"msg": "x"},
                success_criteria={"status": "success"},
                idempotency_key="idem-1",
                risk_level="high",
            )
            state = WorkingState(
                session_id="s3",
                agent_id="router-agent",
                budgets_remaining=BudgetCounters(
                    ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
                ),
                plan=Plan(
                    objective="do risky",
                    steps=[command],
                    stop_conditions=[],
                    assumptions=[],
                    risk_summary="",
                    success_criteria={},
                ),
                cursor=0,
                status="active",
                trace_id="trace-risk",
            )
            session.put_working_state("s3", state_inline=state.model_dump(mode="json"))

            output = runner.step(session_id="s3")
            self.assertEqual(output.status, "waiting_user")
            self.assertIn("requires user confirmation", (output.message or "").lower())
