from tests.brain.runner_test_support import (
    Any,
    BaseModel,
    BudgetCounters,
    Decision,
    DecisionAdapter,
    LocalA2AAdapter,
    LocalContextAdapter,
    LocalMemoryAdapter,
    LocalPolicyAdapter,
    LocalSessionStore,
    LocalToolAdapter,
    MagicMock,
    Path,
    Plan,
    ReflectReport,
    RunnerOptions,
    SimpleNamespace,
    BrainRunner,
    ToolCommand,
    WorkingState,
    _profile,
    build_seeded_act_decision,
    datetime,
    fake_context_builder,
    fake_logger,
    fake_session_api,
    patch,
    tempfile,
    unittest,
)

from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.schemas import (
    LLMRequest,
    LLMResponse,
    Message,
    ToolCall,
    UsageInfo,
)


def _entry_text_response(text: str) -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="test",
        model="decide-default",
        output_text=text,
        assistant_messages=[Message(role="assistant", content=text)],
        tool_calls=[],
        usage=UsageInfo(total_tokens=1),
        finish_reason="stop",
    )


def _entry_clarify_response(question: str) -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="test",
        model="decide-default",
        output_text="",
        assistant_messages=[Message(role="assistant", content="")],
        tool_calls=[ToolCall(name="clarify", arguments={"question": question})],
        usage=UsageInfo(total_tokens=1),
        finish_reason="tool_calls",
    )


def _entry_tool_response(name: str, arguments: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="test",
        model="decide-default",
        output_text="",
        assistant_messages=[Message(role="assistant", content="")],
        tool_calls=[ToolCall(name=name, arguments=arguments)],
        usage=UsageInfo(total_tokens=1),
        finish_reason="tool_calls",
    )


def _empty_entry_response() -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="test",
        model="decide-default",
        output_text="",
        assistant_messages=[Message(role="assistant", content="")],
        tool_calls=[],
        usage=UsageInfo(total_tokens=1),
        finish_reason="stop",
    )


class _StaticEntryLLM:
    def __init__(self, response_or_exc: LLMResponse | Exception) -> None:
        self._response_or_exc = response_or_exc
        self.call_count = 0
        self.last_request: LLMRequest | None = None

    def estimate_tokens(self, *, model: str, context: dict[str, Any]) -> int:
        del model, context
        return 1

    def call(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        self.last_request = request
        if isinstance(self._response_or_exc, Exception):
            raise self._response_or_exc
        return self._response_or_exc


class RunnerDecisionTests(unittest.TestCase):
    def test_normalize_decision_payload_requires_explicit_act_payload_shape(
        self,
    ) -> None:
        runner = BrainRunner(profile=_profile(), session_api=fake_session_api())
        raw = {
            "mode": "act",
            "confidence": 0.8,
            "reason_code": "tool_pick",
            "act_profile": "general",
            "tool_name": "weather.openmeteo.current",
        }

        normalized = runner._normalize_decision_payload(raw)
        with self.assertRaises(Exception):
            DecisionAdapter.validate_python(normalized)

    def test_normalize_decision_payload_keeps_question_response_shape(self) -> None:
        runner = BrainRunner(profile=_profile(), session_api=fake_session_api())
        raw = {
            "mode": "respond",
            "confidence": 0.6,
            "reason_code": "fallback_unstructured_response",
            "respond_kind": "clarify",
            "question": "I do not have real-time weather access.",
        }

        normalized = runner._normalize_decision_payload(raw)
        decision = DecisionAdapter.validate_python(normalized)

        self.assertEqual(decision.mode, "respond")
        self.assertIn("real-time weather", str(decision.question))

    def test_decision_adapter_accepts_optional_clarify_context(self) -> None:
        decision = DecisionAdapter.validate_python(
            {
                "mode": "respond",
                "confidence": 0.8,
                "reason_code": "clarify_weather_scope",
                "respond_kind": "clarify",
                "question": "Did you mean the weather in China, or something else?",
                "clarify_context": {
                    "original_user_input": "what's rather at china?",
                    "inferred_goal": "weather",
                    "known_context": {"place": "China"},
                    "unresolved_question": "Confirm whether the user wants weather information.",
                    "clarify_question": "Did you mean the weather in China, or something else?",
                },
            }
        )

        self.assertEqual(decision.respond_kind, "clarify")
        self.assertIsNotNone(decision.clarify_context)
        self.assertEqual(
            decision.clarify_context.original_user_input,
            "what's rather at china?",
        )
        self.assertEqual(decision.clarify_context.known_context, {"place": "China"})

    def test_normalize_decision_payload_preserves_clarify_context(self) -> None:
        runner = BrainRunner(profile=_profile(), session_api=fake_session_api())
        raw = {
            "mode": "respond",
            "confidence": 0.7,
            "reason_code": "clarify_weather_scope",
            "respond_kind": "clarify",
            "question": "Did you mean the weather in China, or something else?",
            "clarify_context": {
                "original_user_input": "what's rather at china?",
                "inferred_goal": "weather",
                "known_context": {"place": "China"},
                "clarify_question": "Did you mean the weather in China, or something else?",
            },
        }

        normalized = runner._normalize_decision_payload(raw)

        self.assertIn("clarify_context", normalized)
        self.assertEqual(
            normalized["clarify_context"]["known_context"],
            {"place": "China"},
        )
        decision = DecisionAdapter.validate_python(normalized)
        self.assertEqual(decision.clarify_context.inferred_goal, "weather")

    def test_decide_empty_entry_response_fails_closed_before_execution(self) -> None:
        llm_api = _StaticEntryLLM(_empty_entry_response())
        context_api = fake_context_builder()
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=context_api,
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        state = WorkingState(
            session_id="s-placeholder-tool-args",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )

        with (
            patch.object(runner, "_estimate_tokens", return_value=1),
            patch.object(runner, "_debit_tokens", return_value=None),
        ):
            decision = runner._decide(
                state=state,
                user_input="weather in major us cities",
                logger=fake_logger(),
            )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.respond_kind, "answer")
        self.assertEqual(decision.reason_code, "llm_empty_response")
        self.assertIn("internal decision error", str(decision.answer or "").lower())

    def test_decide_confirmation_replay_uses_continuation_guidance_not_goal(
        self,
    ) -> None:
        llm_api = _StaticEntryLLM(_entry_text_response("continue"))
        context_api = fake_context_builder()
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=context_api,
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        original_request = (
            "Create a tiny Python project in the scratch workspace and run pytest."
        )
        continuation_guidance = (
            "Continue from the current confirmed task state. Inspect what was already "
            "written, verify it, and finish without restarting from scratch."
        )
        state = WorkingState(
            session_id="s-confirmation-replay-query",
            agent_id="router-agent",
            goal=original_request,
            last_user_input=original_request,
            decision_reason_code="confirmation_replay",
            post_action_user_message=continuation_guidance,
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )
        captured_hints: dict[str, Any] = {}

        def _capture_context(*, hints: dict[str, Any], **_: Any) -> dict[str, Any]:
            captured_hints.clear()
            captured_hints.update(hints)
            return {"hints": dict(hints)}

        with (
            patch.object(runner, "_estimate_tokens", return_value=1),
            patch.object(runner, "_debit_tokens", return_value=None),
            patch.object(runner, "_build_context", side_effect=_capture_context),
        ):
            decision = runner._decide(
                state=state,
                user_input=None,
                logger=fake_logger(),
            )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(captured_hints.get("user_input"), continuation_guidance)
        self.assertNotEqual(captured_hints.get("user_input"), original_request)

    def test_decide_confirmation_replay_without_guidance_uses_generic_continue_prompt(
        self,
    ) -> None:
        llm_api = _StaticEntryLLM(_entry_text_response("continue"))
        context_api = fake_context_builder()
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=context_api,
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        original_request = "Create a tiny Python project in the scratch workspace."
        state = WorkingState(
            session_id="s-confirmation-replay-generic-query",
            agent_id="router-agent",
            goal=original_request,
            last_user_input=original_request,
            decision_reason_code="confirmation_replay",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )
        captured_hints: dict[str, Any] = {}

        def _capture_context(*, hints: dict[str, Any], **_: Any) -> dict[str, Any]:
            captured_hints.clear()
            captured_hints.update(hints)
            return {"hints": dict(hints)}

        with (
            patch.object(runner, "_estimate_tokens", return_value=1),
            patch.object(runner, "_debit_tokens", return_value=None),
            patch.object(runner, "_build_context", side_effect=_capture_context),
        ):
            runner._decide(
                state=state,
                user_input=None,
                logger=fake_logger(),
            )

        user_input_hint = str(captured_hints.get("user_input") or "")
        self.assertIn("Continue from the current confirmed task state", user_input_hint)
        self.assertNotEqual(user_input_hint, original_request)

    def test_decide_surfaces_provider_rate_limit_instead_of_internal_failure(
        self,
    ) -> None:
        llm_api = _StaticEntryLLM(
            LLMCtlError(
                "RATE_LIMITED",
                'openai rate limited: {"error":{"message":"insufficient balance (1008)","http_code":"429"}}',
                {"status_code": 429},
            )
        )
        context_api = fake_context_builder()
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=context_api,
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        state = WorkingState(
            session_id="s-provider-rate-limit",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )

        # AR-04 (2026-06-18): RATE_LIMITED is now a retryable category, so
        # the entry call retries with backoff before surfacing the typed
        # provider-failure decision. Patch the backoff sleep to keep the
        # test fast; the final decision is unchanged (a persistent
        # rate-limit still surfaces `provider_rate_limited` after retries).
        with (
            patch.object(runner, "_estimate_tokens", return_value=1),
            patch.object(runner, "_debit_tokens", return_value=None),
            patch(
                "openminion.modules.brain.loop.orchestration._BACKOFF_SLEEP",
                lambda *_a, **_k: None,
            ),
        ):
            decision = runner._decide(
                state=state,
                user_input="hi",
                logger=fake_logger(),
            )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.reason_code, "provider_rate_limited")
        self.assertIn("quota", str(decision.answer or "").lower())
        self.assertIn("insufficient balance", str(decision.answer or "").lower())
        # AR-04: the entry call is attempted 3 times (1 + 2 retries) before
        # the persistent rate-limit is surfaced.
        self.assertEqual(llm_api.call_count, 3)

    def test_decide_retries_transient_provider_error_then_succeeds(self) -> None:
        """AR-04 (2026-06-18): a single transient provider error (502 /
        PROVIDER_ERROR) on the entry call is absorbed by the typed retry
        policy — the turn recovers instead of failing closed."""

        class _SequenceEntryLLM:
            def __init__(self, sequence: list[Any]) -> None:
                self._sequence = list(sequence)
                self.call_count = 0

            def estimate_tokens(self, *, model: str, context: dict[str, Any]) -> int:
                del model, context
                return 1

            def call(self, request: LLMRequest) -> LLMResponse:
                del request
                item = self._sequence[self.call_count]
                self.call_count += 1
                if isinstance(item, Exception):
                    raise item
                return item

        llm_api = _SequenceEntryLLM(
            [
                LLMCtlError("PROVIDER_ERROR", "Bad Gateway", {"status_code": 502}),
                _entry_text_response("recovered after transient 502"),
            ]
        )
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=fake_context_builder(),
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        state = WorkingState(
            session_id="s-transient-502",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )
        sleep_calls: list[float] = []

        with (
            patch.object(runner, "_estimate_tokens", return_value=1),
            patch.object(runner, "_debit_tokens", return_value=None),
            patch(
                "openminion.modules.brain.loop.orchestration._BACKOFF_SLEEP",
                side_effect=lambda seconds: sleep_calls.append(seconds),
            ),
        ):
            decision = runner._decide(
                state=state,
                user_input="hi",
                logger=fake_logger(),
            )

        # The turn recovered on the second attempt.
        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.reason_code, "entry_text_response")
        self.assertEqual(llm_api.call_count, 2)
        # Exactly one backoff sleep occurred between the two attempts.
        self.assertEqual(len(sleep_calls), 1)
        self.assertGreater(sleep_calls[0], 0.0)

    def test_decide_does_not_retry_invalid_argument(self) -> None:
        """AR-04 (2026-06-18): a deterministic 400 / INVALID_ARGUMENT fault
        is NOT retried — retrying a malformed request just burns budget.
        The typed provider-failure decision surfaces on the first attempt."""
        llm_api = _StaticEntryLLM(
            LLMCtlError(
                "INVALID_ARGUMENT",
                "bad request: unsupported parameter",
                {"status_code": 400},
            )
        )
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=fake_context_builder(),
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        state = WorkingState(
            session_id="s-invalid-argument",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )
        sleep_calls: list[float] = []

        with (
            patch.object(runner, "_estimate_tokens", return_value=1),
            patch.object(runner, "_debit_tokens", return_value=None),
            patch(
                "openminion.modules.brain.loop.orchestration._BACKOFF_SLEEP",
                side_effect=lambda seconds: sleep_calls.append(seconds),
            ),
        ):
            decision = runner._decide(
                state=state,
                user_input="hi",
                logger=fake_logger(),
            )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.reason_code, "provider_invalid_request")
        # Fail-fast: exactly one attempt, no backoff sleep.
        self.assertEqual(llm_api.call_count, 1)
        self.assertEqual(sleep_calls, [])

    def test_decide_new_input_supersedes_existing_plan(self) -> None:
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        pending = ToolCommand(
            title="pending weather",
            tool_name="weather.openmeteo.current",
            args={"city": "san francisco"},
            success_criteria={"status": "success"},
            idempotency_key="pending-weather",
        )
        state = WorkingState(
            session_id="s-supersede",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
            plan=Plan(
                objective="old objective",
                steps=[pending],
                stop_conditions=[],
                assumptions=[],
                risk_summary="",
                success_criteria={},
            ),
            cursor=0,
            status="active",
            goal="old objective",
        )

        decision = runner._decide(
            state=state, user_input="list all tools", logger=fake_logger()
        )
        self.assertNotEqual(decision.reason_code, "resume_existing_plan")
        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.respond_kind, "answer")
        self.assertEqual(decision.reason_code, "llm_unavailable")

    def test_decide_forced_capability_without_explicit_args_fails_closed(self) -> None:
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            tool_api=SimpleNamespace(
                registry=SimpleNamespace(
                    _tools={
                        "web.search": SimpleNamespace(
                            name="web.search",
                            description="Search the web",
                            parameters={
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                                "required": ["query"],
                            },
                        )
                    }
                )
            ),
        )
        state = WorkingState(
            session_id="s-inventory-priority",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
            status="active",
            goal="old objective",
        )

        decision = runner._decide(
            state=state,
            user_input="what tools can you use right now?",
            logger=fake_logger(),
            capability_category="web.search",
        )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.respond_kind, "answer")
        self.assertEqual(decision.reason_code, "llm_unavailable")

    def test_decide_memory_policy_question_uses_llm_path(self) -> None:
        llm_api = _StaticEntryLLM(
            _entry_text_response("Memory policy comes from runtime metadata.")
        )
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=fake_context_builder(),
            options=RunnerOptions(memory_policy_snapshot={}),
        )
        state = WorkingState(
            session_id="s-memory-policy",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
            status="active",
            goal="memory policy check",
        )

        decision = runner._decide(
            state=state,
            user_input="what is your memory retention and refresh policy?",
            logger=fake_logger(),
        )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.reason_code, "entry_text_response")
        self.assertIn("runtime metadata", str(decision.answer or ""))
        self.assertEqual(llm_api.call_count, 1)

    def test_decide_memory_policy_question_without_llm_context_fails_closed(
        self,
    ) -> None:
        llm_api = SimpleNamespace(
            call_structured=MagicMock(
                side_effect=AssertionError("llm should not be called")
            ),
            estimate_tokens=MagicMock(return_value=1),
        )
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,  # no context_api => heuristic fallback path
            options=RunnerOptions(memory_policy_snapshot={}),
        )
        state = WorkingState(
            session_id="s-memory-policy-missing",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
            status="active",
            goal="memory policy check",
        )

        decision = runner._decide(
            state=state,
            user_input="do you remember across sessions and what is the policy?",
            logger=fake_logger(),
        )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.respond_kind, "answer")
        self.assertEqual(decision.reason_code, "llm_unavailable")

    def test_decide_resume_text_no_longer_shortcuts_existing_plan(self) -> None:
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        pending = ToolCommand(
            title="pending weather",
            tool_name="weather.openmeteo.current",
            args={"city": "san francisco"},
            success_criteria={"status": "success"},
            idempotency_key="pending-weather",
        )
        state = WorkingState(
            session_id="s-resume",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
            plan=Plan(
                objective="old objective",
                steps=[pending],
                stop_conditions=[],
                assumptions=[],
                risk_summary="",
                success_criteria={},
            ),
            cursor=0,
            status="active",
            goal="old objective",
        )

        decision = runner._decide(
            state=state, user_input="resume", logger=fake_logger()
        )
        self.assertNotEqual(decision.reason_code, "resume_existing_plan")
        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.respond_kind, "answer")
        self.assertEqual(decision.reason_code, "llm_unavailable")

    def test_decide_explicit_tool_command_requires_llm_decision(self) -> None:
        llm_api = _StaticEntryLLM(_entry_text_response("Tool request acknowledged."))
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=fake_context_builder(),
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        state = WorkingState(
            session_id="s-explicit-tool",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )

        decision = runner._decide(
            state=state,
            user_input='tool file.list_dir {"path":"."}',
            logger=fake_logger(),
        )

        self.assertEqual(decision.mode, "act")
        self.assertEqual(decision.reason_code, "explicit_tool_command")
        seeded = list(getattr(decision, "_seeded_commands", []) or [])
        self.assertEqual(len(seeded), 1)
        command = seeded[0]
        self.assertEqual(command.kind, "tool")
        self.assertEqual(getattr(command, "tool_name", ""), "file.list_dir")
        self.assertEqual(getattr(command, "args", {}).get("path"), ".")
        self.assertEqual(llm_api.call_count, 1)

    def test_decide_text_response_no_longer_requires_act_payload_shape(self) -> None:
        llm_api = _StaticEntryLLM(
            _entry_text_response("Latest news requires a web search.")
        )
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=fake_context_builder(),
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        state = WorkingState(
            session_id="s-string-tool-expression",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )

        decision = runner._decide(
            state=state,
            user_input="latest news on Iran",
            logger=fake_logger(),
        )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.respond_kind, "answer")
        self.assertEqual(decision.reason_code, "entry_text_response")
        self.assertIn("web search", str(decision.answer or "").lower())

    def test_decide_does_not_rescue_weather_tool_when_llm_responds_conversationally(
        self,
    ) -> None:
        llm_api = _StaticEntryLLM(
            _entry_text_response("I don't have real-time weather access.")
        )
        context_api = fake_context_builder()
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=context_api,
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        state = WorkingState(
            session_id="s-weather-rescue",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )

        decision = runner._decide(
            state=state,
            user_input="what's weather at san diego today?",
            logger=fake_logger(),
        )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.reason_code, "entry_text_response")

    def test_decide_does_not_rescue_weather_tool_when_llm_returns_question_fallback(
        self,
    ) -> None:
        llm_api = _StaticEntryLLM(
            _entry_clarify_response("I do not have access to real-time weather.")
        )
        context_api = fake_context_builder()
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=context_api,
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        state = WorkingState(
            session_id="s-weather-rescue-question",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )

        decision = runner._decide(
            state=state,
            user_input="what's weather at sf?",
            logger=fake_logger(),
        )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.reason_code, "entry_clarify")
        self.assertEqual(decision.respond_kind, "clarify")

    def test_decide_handles_non_actionable_llm_response_without_nl_rescue(self) -> None:
        llm_api = _StaticEntryLLM(_entry_text_response("Could you clarify?"))
        context_api = fake_context_builder()
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=context_api,
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        state = WorkingState(
            session_id="s-nl-ask-fallback",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )

        decision = runner._decide(
            state=state,
            user_input="what's weather today?",
            logger=fake_logger(),
        )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.reason_code, "entry_text_response")
        self.assertEqual(decision.respond_kind, "answer")

    def test_decide_rewrites_blocked_tool_envelope_to_safe_clarify(self) -> None:
        llm_api = _StaticEntryLLM(
            _entry_text_response(
                "[system: UNEXECUTABLE_TOOL_ENVELOPE]\n"
                "The model generated a tool envelope that could not be executed."
            )
        )
        context_api = fake_context_builder()
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=context_api,
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        state = WorkingState(
            session_id="s-envelope-safe-rewrite",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )

        logger = MagicMock()
        with (
            patch.object(runner, "_estimate_tokens", return_value=1),
            patch.object(runner, "_debit_tokens", return_value=None),
        ):
            decision = runner._decide(
                state=state,
                user_input="tell me a joke",
                logger=logger,
            )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.reason_code, "entry_text_response")
        self.assertIn("unexecutable_tool_envelope", str(decision.answer).lower())

    def test_decide_invalid_structured_output_emits_fail_closed_telemetry(self) -> None:
        llm_api = _StaticEntryLLM(_empty_entry_response())
        context_api = fake_context_builder()
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=context_api,
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        state = WorkingState(
            session_id="s-invalid-decide-telemetry",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )
        logger = MagicMock()
        with (
            patch.object(runner, "_estimate_tokens", return_value=1),
            patch.object(runner, "_debit_tokens", return_value=None),
        ):
            decision = runner._decide(
                state=state,
                user_input="do something complex",
                logger=logger,
            )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.reason_code, "llm_empty_response")
        fail_closed_events = [
            call.args[1]
            for call in logger.emit.call_args_list
            if call.args and call.args[0] == "brain.fail_closed.decide_invalid_output"
        ]
        self.assertTrue(fail_closed_events)
        self.assertEqual(fail_closed_events[-1]["reason_code"], "internal_failure")

    def test_validation_fallback_stays_safe_when_act_payload_is_invalid(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=None,
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(metactl_enabled=False),
            )
            invalid = SimpleNamespace(
                **build_seeded_act_decision(
                    command=ToolCommand(
                        title="start browser",
                        tool_name="browser",
                        args={"op": "instance.start"},
                        success_criteria={"status": "success"},
                        idempotency_key="validator-failure",
                    ),
                    confidence=1.0,
                    reason_code="first_invalid",
                    act_profile="general",
                    sub_intents=["start_browser", "navigate_to_url"],
                ).model_dump(mode="json"),
                _seeded_commands=[
                    ToolCommand(
                        title="start browser",
                        tool_name="browser",
                        args={"op": "instance.start"},
                        success_criteria={"status": "success"},
                        idempotency_key="validator-failure",
                    )
                ],
            )
            invalid.execution_target = "local"

            with patch.object(runner, "_decide", side_effect=[invalid, invalid]):
                output = runner.step(
                    session_id="s-validation-fail-closed",
                    user_input="open browser and go to example.com",
                    trace_id="trace-validation-fail-closed",
                )

        self.assertEqual(output.status, "waiting_user")
        self.assertIn(
            "i no longer have an active plan for that result",
            str(output.message or "").lower(),
        )
        self.assertNotIn("clarify or rephrase", str(output.message or "").lower())

    def test_decide_does_not_runtime_normalize_pure_greeting_clarify_output(
        self,
    ) -> None:
        for reason_code in ("ambiguous_intent", "greeting_ambiguous"):
            with self.subTest(reason_code=reason_code):
                llm_api = _StaticEntryLLM(
                    _entry_clarify_response(
                        "Could you please rephrase or provide more context? "
                        "I'm not sure how to best assist with just the word "
                        '"hello?".'
                    )
                )
                context_api = fake_context_builder()
                runner = BrainRunner(
                    profile=_profile(),
                    session_api=fake_session_api(),
                    llm_api=llm_api,
                    context_api=context_api,
                    tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
                )
                state = WorkingState(
                    session_id=f"s-greeting-normalized-{reason_code}",
                    agent_id="router-agent",
                    budgets_remaining=BudgetCounters(
                        ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
                    ),
                )
                logger = MagicMock()

                with (
                    patch.object(runner, "_estimate_tokens", return_value=1),
                    patch.object(runner, "_debit_tokens", return_value=None),
                ):
                    decision = runner._decide(
                        state=state,
                        user_input="hello?",
                        logger=logger,
                    )

                self.assertEqual(decision.mode, "respond")
                self.assertEqual(decision.respond_kind, "clarify")
                self.assertEqual(decision.reason_code, "entry_clarify")
                self.assertIn("context", str(decision.question).lower())
                self.assertFalse(
                    any(
                        call.args
                        and call.args[0] == "brain.decision.greeting_normalized"
                        for call in logger.emit.call_args_list
                    )
                )

    def test_decide_does_not_normalize_non_greeting_ambiguous_request(self) -> None:
        llm_api = _StaticEntryLLM(
            _entry_clarify_response(
                "Could you clarify whether you mean session memory or a previous message?"
            )
        )
        context_api = fake_context_builder()
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=context_api,
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        state = WorkingState(
            session_id="s-greeting-negative",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )
        logger = MagicMock()

        with (
            patch.object(runner, "_estimate_tokens", return_value=1),
            patch.object(runner, "_debit_tokens", return_value=None),
        ):
            decision = runner._decide(
                state=state,
                user_input="hey do you remember me?",
                logger=logger,
            )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.respond_kind, "clarify")
        self.assertEqual(decision.reason_code, "entry_clarify")
        self.assertIn("clarify", str(decision.question).lower())
        self.assertFalse(
            any(
                call.args and call.args[0] == "brain.decision.greeting_normalized"
                for call in logger.emit.call_args_list
            )
        )

    def test_decide_entry_tool_call_routes_directly_to_act(self) -> None:
        llm_api = _StaticEntryLLM(_entry_tool_response("time", {"timezone": "UTC"}))
        context_api = fake_context_builder()
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=context_api,
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        state = WorkingState(
            session_id="s-greeting-plan-normalized",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )
        logger = MagicMock()

        with (
            patch.object(runner, "_estimate_tokens", return_value=1),
            patch.object(runner, "_debit_tokens", return_value=None),
        ):
            decision = runner._decide(
                state=state,
                user_input="hey",
                logger=logger,
            )

        self.assertEqual(decision.mode, "act")
        self.assertEqual(decision.reason_code, "entry_tool_call")
        self.assertIsNotNone(getattr(decision, "_entry_response", None))
        bootstrap_route = getattr(decision, "_pre_resolved_act_route", None)
        self.assertIsNotNone(bootstrap_route)
        self.assertEqual(getattr(bootstrap_route, "act_profile", ""), "general")
        self.assertFalse(
            any(
                call.args and call.args[0] == "brain.decision.greeting_normalized"
                for call in logger.emit.call_args_list
            )
        )

    def test_forced_tool_missing_args_asks_user(self) -> None:
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            tool_api=SimpleNamespace(
                registry=SimpleNamespace(_tools={"weather": object()})
            ),
        )
        state = WorkingState(
            session_id="s-removal-regression-guard",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )
        logger = MagicMock()
        decision = runner._decide(
            state=state,
            user_input="weather today",
            logger=logger,
            forced_tools=["weather"],
        )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.respond_kind, "answer")
        self.assertEqual(decision.reason_code, "llm_unavailable")

    def test_decide_recovers_time_canary_when_capability_selected(self) -> None:
        llm_api = _StaticEntryLLM(_entry_tool_response("time", {"timezone": "UTC"}))
        context_api = fake_context_builder()
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=context_api,
            tool_api=SimpleNamespace(
                registry=SimpleNamespace(_tools={"time": object()})
            ),
        )
        state = WorkingState(
            session_id="s-time-canary",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )

        decision = runner._decide(
            state=state,
            user_input="what time is it in UTC right now?",
            capability_category="time",
            logger=fake_logger(),
        )

        self.assertEqual(decision.mode, "act")
        self.assertEqual(decision.reason_code, "entry_tool_call")

    def test_decide_recovers_weather_canary_after_provider_refusal(self) -> None:
        llm_api = _StaticEntryLLM(
            _entry_text_response("I do not have real-time weather access.")
        )
        context_api = fake_context_builder()
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=context_api,
            tool_api=SimpleNamespace(
                registry=SimpleNamespace(_tools={"weather": object()})
            ),
        )
        state = WorkingState(
            session_id="s-weather-canary",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )

        decision = runner._decide(
            state=state,
            user_input="what is the weather in San Francisco right now?",
            capability_category="weather",
            logger=fake_logger(),
        )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.respond_kind, "answer")
        self.assertEqual(decision.reason_code, "entry_text_response")

    def test_decide_weather_parity_recovery_still_fails_closed_when_ambiguous(
        self,
    ) -> None:
        llm_api = _StaticEntryLLM(_entry_clarify_response("Please clarify."))
        context_api = fake_context_builder()
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            llm_api=llm_api,
            context_api=context_api,
            tool_api=SimpleNamespace(
                registry=SimpleNamespace(_tools={"weather": object()})
            ),
        )
        state = WorkingState(
            session_id="s-weather-ambiguous",
            agent_id="router-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
            ),
        )

        decision = runner._decide(
            state=state,
            user_input="what is the weather right now?",
            capability_category="weather",
            logger=fake_logger(),
        )

        self.assertEqual(decision.mode, "respond")
        self.assertEqual(decision.respond_kind, "clarify")
        self.assertEqual(decision.reason_code, "entry_clarify")

    def test_prompt_tool_schemas_disabled_by_default(self) -> None:
        runner = BrainRunner(
            profile=_profile(),
            session_api=fake_session_api(),
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        )
        self.assertFalse(runner._prompt_tool_schemas_enabled)

    def test_prompt_tool_schemas_env_enabled(self) -> None:
        with patch.dict("os.environ", {"OPENMINION_PROMPT_TOOL_SCHEMAS": "1"}):
            runner = BrainRunner(
                profile=_profile(),
                session_api=fake_session_api(),
                tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
            )
        self.assertTrue(runner._prompt_tool_schemas_enabled)

    def test_collect_runtime_tool_schemas_reads_openminion_registry_parameters(
        self,
    ) -> None:
        class _WeatherTool:
            name = "weather"
            description = "Get current weather by city name."
            parameters = {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
                "additionalProperties": False,
            }

        tool_api = SimpleNamespace(
            registry=SimpleNamespace(_tools={"weather": _WeatherTool()})
        )
        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=tool_api
        )
        schemas = runner._collect_runtime_tool_schemas()

        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["name"], "weather")
        self.assertEqual(schemas[0]["description"], "Get current weather by city name.")
        self.assertEqual(schemas[0]["parameters"].get("required"), ["city"])

    def test_collect_runtime_tool_schemas_reads_openminion_tool_args_model(
        self,
    ) -> None:
        class _WeatherArgs(BaseModel):
            city: str

        class _ToolSpec:
            name = "weather"
            args_model = _WeatherArgs

        class _Registry:
            def list(self):
                return {"weather": _ToolSpec()}

        tool_api = SimpleNamespace(registry=_Registry())
        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=tool_api
        )
        schemas = runner._collect_runtime_tool_schemas()

        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["name"], "weather")
        self.assertEqual(schemas[0]["parameters"].get("type"), "object")
        self.assertIn("city", schemas[0]["parameters"].get("properties", {}))

    def test_build_prompt_tool_schemas_shortlists_and_stubs(self) -> None:
        class _Registry:
            def __init__(self) -> None:
                self._tools = {}
                names = [
                    "utility.calculate_expression",
                    "file.find",
                    "http_request",
                    "file.list_dir",
                    "weather",
                    "process_output",
                    "process_status",
                    "file.read",
                    "exec.run",
                    "start_process",
                    "stop_process",
                    "text_stats",
                    "time",
                    "web.fetch",
                    "web.search",
                    "file.write",
                ]
                for name in names:
                    self._tools[name] = SimpleNamespace(
                        name=name,
                        description=f"desc for {name}",
                        parameters={
                            "type": "object",
                            "properties": {
                                "required_arg": {
                                    "type": "string",
                                    "description": "must pass",
                                },
                                "optional_arg": {
                                    "type": "string",
                                    "description": "optional",
                                },
                            },
                            "required": ["required_arg"],
                            "additionalProperties": False,
                        },
                    )

        tool_api = SimpleNamespace(registry=_Registry())
        runner = BrainRunner(
            profile=_profile(), session_api=fake_session_api(), tool_api=tool_api
        )

        generic = runner._build_prompt_tool_schemas(user_input="test")
        self.assertEqual(
            [entry["name"] for entry in generic],
            [
                "exec.run",
                "file.find",
                "file.list_dir",
                "file.read",
                "file.write",
                "http_request",
                "process_output",
                "process_status",
            ],
        )
        self.assertEqual(
            set(generic[0]["parameters"].get("properties", {}).keys()), {"required_arg"}
        )

        targeted = runner._build_prompt_tool_schemas(user_input="different text")
        self.assertEqual(targeted, generic)

    def test_pre_llm_identity_audit_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")

            from pydantic import BaseModel

            # Create a minimal working test setup with mocked LLM
            class MockLLMAPI:
                contract_version = "v1"

                def estimate_tokens(self, *, model: str, context: dict) -> int:
                    return 250

                def call_structured(
                    self,
                    *,
                    model: str,
                    purpose: str,
                    context: dict,
                    schema: type[BaseModel],
                ) -> dict:
                    # Return mock response based on schema
                    if schema == Decision:
                        return {
                            "mode": "respond",
                            "confidence": 0.8,
                            "reason_code": "test_mock",
                            "respond_kind": "answer",
                            "answer": "test response",
                        }
                    if schema == Plan:
                        return {
                            "objective": "test objective",
                            "steps": [],
                            "stop_conditions": [],
                            "assumptions": [],
                            "risk_summary": "low",
                            "success_criteria": {},
                        }
                    if schema == ReflectReport:
                        return {
                            "outcome": "success",
                            "fixes": [],
                            "recommendations": [],
                            "confidence_score": 0.8,
                        }

            profile = _profile()
            BrainRunner(
                profile=profile,
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=MockLLMAPI(),
                tool_api=LocalToolAdapter(),  # Use existing from imports
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(reflection_enabled=True),
            )

    def test_decide_trims_tool_schema_context_before_token_budget_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")

            class _LargeRegistry:
                def __init__(self) -> None:
                    self._tools = {}
                    for idx in range(80):
                        name = f"tool.large.{idx}"
                        self._tools[name] = SimpleNamespace(
                            name=name,
                            description=f"large tool {idx}",
                            parameters={
                                "type": "object",
                                "properties": {
                                    "arg_a": {"type": "string", "description": "a"},
                                    "arg_b": {"type": "string", "description": "b"},
                                    "arg_c": {"type": "string", "description": "c"},
                                },
                                "required": ["arg_a", "arg_b"],
                                "additionalProperties": False,
                            },
                        )

            class _BudgetAwareLLM:
                contract_version = "v1"

                def estimate_tokens(
                    self, *, model: str, context: dict[str, Any]
                ) -> int:
                    del model
                    hints = (
                        context.get("hints", {}) if isinstance(context, dict) else {}
                    )
                    schemas = (
                        hints.get("runtime_tool_schemas")
                        if isinstance(hints, dict)
                        else []
                    )
                    count = len(schemas) if isinstance(schemas, list) else 0
                    if count >= 50:
                        return 500
                    if count > 0:
                        return 80
                    return 40

                def call(self, request: LLMRequest) -> LLMResponse:
                    del request
                    return _entry_text_response("ok")

            profile = _profile()
            profile.budgets.max_total_llm_tokens = 60
            runner = BrainRunner(
                profile=profile,
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=_BudgetAwareLLM(),
                tool_api=SimpleNamespace(registry=_LargeRegistry()),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(
                    metactl_enabled=False,
                    plan_auto_scale_max_ticks=1,
                ),
            )
            state = runner._load_or_init_state("s-budget-trim")
            state.budgets_remaining.tokens = 60
            state.trace_id = "trace-budget-trim"
            logger = MagicMock()

            decision = runner._decide(
                state=state,
                user_input="hey",
                logger=logger,
            )

            self.assertEqual(decision.mode, "respond")
            self.assertEqual(decision.reason_code, "entry_text_response")
            self.assertGreaterEqual(state.llm_calls_used, 1)
            emits = [call.args[0] for call in logger.emit.call_args_list]
            self.assertNotIn("llm.context.trimmed", emits)

            # Test that identity audit events are emitted for _decide (decision)
            runner.step(
                session_id="s-identity-audit",
                user_input="say hello",
                trace_id="trace-identity",
            )

            events = session.list_events("s-identity-audit")
            event_types = {event["type"] for event in events}

            # Check if the identity audit events were emitted
            self.assertIn("llm.identity_audit", event_types)

            # Verify that each identity audit has the required fields
            identity_events = [e for e in events if e["type"] == "llm.identity_audit"]
            self.assertGreaterEqual(
                len(identity_events), 1
            )  # At least one LLM call made

            for event in identity_events:
                payload = event["payload"]
                self.assertIn("llm_call_id", payload)
                self.assertIn("purpose", payload)
                self.assertIn("agent_id", payload)
                self.assertIn("profile_version", payload)
                self.assertIn("trace_id", payload)
                self.assertEqual(payload["agent_id"], profile.agent_id)
                # Verify that purpose is one of the expected values for LLM calls
                self.assertIn(payload["purpose"], ["entry", "plan", "reflect"])

    def test_decide_trims_runtime_tool_schemas_in_three_stages_before_llm_call(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")

            class _VerboseRegistry:
                def __init__(self) -> None:
                    self._tools = {}
                    for idx in range(24):
                        name = f"tool.verbose.{idx}"
                        self._tools[name] = SimpleNamespace(
                            name=name,
                            description=f"verbose tool {idx} " + ("x" * 700),
                            parameters={
                                "type": "object",
                                "properties": {
                                    "arg_a": {"type": "string", "description": "alpha"},
                                    "arg_b": {"type": "string", "description": "beta"},
                                    "arg_c": {"type": "string", "description": "gamma"},
                                },
                                "required": ["arg_a", "arg_b"],
                                "additionalProperties": False,
                            },
                        )

            class _TwoStageTrimLLM:
                contract_version = "v1"

                def __init__(self) -> None:
                    self.call_count = 0

                def estimate_tokens(
                    self, *, model: str, context: dict[str, Any]
                ) -> int:
                    del model
                    hints = (
                        context.get("hints", {}) if isinstance(context, dict) else {}
                    )
                    schemas = (
                        hints.get("runtime_tool_schemas")
                        if isinstance(hints, dict)
                        else []
                    )
                    if not isinstance(schemas, list) or not schemas:
                        return 40
                    max_description_len = max(
                        len(str(item.get("description", "")))
                        for item in schemas
                        if isinstance(item, dict)
                    )
                    if max_description_len > 300:
                        return 500
                    return 80

                def call(self, request: LLMRequest) -> LLMResponse:
                    del request
                    self.call_count += 1
                    return _entry_text_response("ok")

            profile = _profile()
            profile.budgets.max_total_llm_tokens = 60
            llm = _TwoStageTrimLLM()
            runner = BrainRunner(
                profile=profile,
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=llm,
                tool_api=SimpleNamespace(registry=_VerboseRegistry()),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(metactl_enabled=False),
            )
            state = runner._load_or_init_state("s-budget-trim-two-stage")
            state.budgets_remaining.tokens = 60
            state.trace_id = "trace-budget-trim-two-stage"
            logger = MagicMock()

            decision = runner._decide(
                state=state,
                user_input="hey",
                logger=logger,
            )

            self.assertEqual(decision.mode, "respond")
            self.assertEqual(decision.reason_code, "entry_text_response")
            self.assertEqual(llm.call_count, 1)
            self.assertGreaterEqual(state.llm_calls_used, 1)

            trim_events = [
                call
                for call in logger.emit.call_args_list
                if call.args and call.args[0] == "llm.context.trimmed"
            ]
            self.assertEqual(trim_events, [])

    def test_decide_keeps_shortlisted_runtime_tool_schemas_when_budget_allows(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")

            class _VerboseRegistry:
                def __init__(self) -> None:
                    self._tools = {}
                    for idx in range(24):
                        name = f"tool.verbose.{idx}"
                        self._tools[name] = SimpleNamespace(
                            name=name,
                            description=f"verbose tool {idx} " + ("x" * 700),
                            parameters={
                                "type": "object",
                                "properties": {
                                    "arg_a": {"type": "string", "description": "alpha"},
                                    "arg_b": {"type": "string", "description": "beta"},
                                    "arg_c": {"type": "string", "description": "gamma"},
                                },
                                "required": ["arg_a", "arg_b"],
                                "additionalProperties": False,
                            },
                        )

            class _ShortlistBudgetLLM:
                contract_version = "v1"

                def __init__(self) -> None:
                    self.call_count = 0
                    self.runtime_tool_counts: list[int] = []

                def estimate_tokens(
                    self, *, model: str, context: dict[str, Any]
                ) -> int:
                    del model
                    hints = (
                        context.get("hints", {}) if isinstance(context, dict) else {}
                    )
                    schemas = (
                        hints.get("runtime_tool_schemas")
                        if isinstance(hints, dict)
                        else []
                    )
                    count = len(schemas) if isinstance(schemas, list) else 0
                    if count >= 20:
                        return 500
                    if count > 8:
                        return 100
                    if count > 0:
                        return 40
                    return 20

                def call(self, request: LLMRequest) -> LLMResponse:
                    self.call_count += 1
                    tools = list(getattr(request, "tools", []) or [])
                    self.runtime_tool_counts.append(len(tools))
                    return _entry_text_response("ok")

            profile = _profile()
            profile.budgets.max_total_llm_tokens = 60
            llm = _ShortlistBudgetLLM()
            runner = BrainRunner(
                profile=profile,
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=llm,
                tool_api=SimpleNamespace(registry=_VerboseRegistry()),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(metactl_enabled=False),
            )
            state = runner._load_or_init_state("s-budget-trim-shortlist")
            state.budgets_remaining.tokens = 60
            state.trace_id = "trace-budget-trim-shortlist"
            logger = MagicMock()

            decision = runner._decide(
                state=state,
                user_input="hey",
                logger=logger,
            )

            self.assertEqual(decision.mode, "respond")
            self.assertEqual(decision.reason_code, "entry_text_response")
            self.assertEqual(llm.call_count, 1)
            self.assertTrue(llm.runtime_tool_counts)
            self.assertGreater(llm.runtime_tool_counts[0], 0)
            self.assertLess(
                llm.runtime_tool_counts[0],
                24,
                "shortlisting should trim below the 24-tool verbose registry size",
            )

            trim_events = [
                call
                for call in logger.emit.call_args_list
                if call.args and call.args[0] == "llm.context.trimmed"
            ]
            self.assertEqual(trim_events, [])

    def test_decide_returns_token_budget_exceeded_when_context_remains_over_budget(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")

            class _AlwaysHighEstimateLLM:
                contract_version = "v1"

                def __init__(self) -> None:
                    self.call_count = 0

                def estimate_tokens(
                    self, *, model: str, context: dict[str, Any]
                ) -> int:
                    del model, context
                    return 120

                def call(self, request: LLMRequest) -> LLMResponse:
                    del request
                    self.call_count += 1
                    raise AssertionError(
                        "call should not be invoked on budget hard-fail"
                    )

            profile = _profile()
            profile.budgets.max_total_llm_tokens = 60
            llm = _AlwaysHighEstimateLLM()
            runner = BrainRunner(
                profile=profile,
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=llm,
                tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(metactl_enabled=False),
            )
            state = runner._load_or_init_state("s-budget-hard-fail")
            state.budgets_remaining.tokens = 60
            state.trace_id = "trace-budget-hard-fail"
            logger = MagicMock()

            decision = runner._decide(
                state=state,
                user_input="hey",
                logger=logger,
            )

            self.assertEqual(decision.mode, "respond")
            self.assertEqual(decision.respond_kind, "answer")
            self.assertEqual(decision.reason_code, "token_budget_exceeded")
            self.assertIn("token budget", str(decision.answer).lower())
            self.assertEqual(llm.call_count, 0)
            self.assertEqual(state.llm_calls_used, 0)

    def test_decide_low_budget_with_memory_v2_hello_world_snapshot_still_responds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")

            class _LowBudgetLLM:
                contract_version = "v1"

                def estimate_tokens(
                    self, *, model: str, context: dict[str, Any]
                ) -> int:
                    del model, context
                    return 40

                def call(self, request: LLMRequest) -> LLMResponse:
                    del request
                    return _entry_text_response("ok")

            profile = _profile()
            profile.budgets.max_total_llm_tokens = 60
            runner = BrainRunner(
                profile=profile,
                session_api=session,
                context_api=LocalContextAdapter(session_store=session),
                llm_api=_LowBudgetLLM(),
                tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(
                    metactl_enabled=False,
                    memory_policy_snapshot={
                        "policy_source": "runtime.config",
                        "policy_version": "memory_policy_snapshot.v1",
                        "memory_enabled": True,
                        "memory_provider": "memory_v2_hello_world",
                        "capsule_strategy": "dynamic_turn",
                        "refresh_policy": "refresh_each_turn",
                        "dynamic_retrieval_enabled": True,
                        "retention_days": 30,
                        "session_vs_cross_session": "session_plus_cross_session",
                    },
                ),
            )
            state = runner._load_or_init_state("s-low-budget-hello-world")
            state.budgets_remaining.tokens = 60
            decision = runner._decide(
                state=state,
                user_input="hey",
                logger=fake_logger(),
            )

            self.assertEqual(decision.mode, "respond")
            self.assertEqual(decision.reason_code, "entry_text_response")

    def test_decide_context_includes_current_datetime(self) -> None:
        class _CapturingContextAdapter(LocalContextAdapter):
            def __init__(self, *, session_store) -> None:
                super().__init__(session_store=session_store)
                self.last_context: dict[str, Any] | None = None

            def build(self, **kwargs):  # type: ignore[override]
                context = super().build(**kwargs)
                self.last_context = context
                return context

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            context_api = _CapturingContextAdapter(session_store=session)
            llm = _StaticEntryLLM(_entry_text_response("ok"))
            runner = BrainRunner(
                profile=_profile(),
                session_api=session,
                context_api=context_api,
                llm_api=llm,
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(metactl_enabled=False),
            )
            state = runner._load_or_init_state("s-bape-07-decide-datetime")
            decision = runner._decide(
                state=state,
                user_input="what should we do today?",
                logger=fake_logger(),
            )

            self.assertEqual(decision.mode, "respond")
            hints = (context_api.last_context or {}).get("hints", {})
            current_datetime = str(hints.get("current_datetime") or "")
            self.assertTrue(current_datetime)
            self.assertIsNotNone(datetime.fromisoformat(current_datetime))
